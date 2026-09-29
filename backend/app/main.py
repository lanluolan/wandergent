"""FastAPI app entry point.

`POST /plan/stream` is the primary path and `POST /plan` its non-streaming fallback;
both share one orchestrator. `/health` is a liveness probe and answers without any
external dependency.
"""

import hashlib
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import mail
from app import ratelimit as ratelimits
from app.agent.events import PlanEvent
from app.agent.orchestrator import (
    PlanningConfigError,
    PlanningError,
    PlanningTimeout,
    PlanResult,
    plan_trip,
    stream_plan,
)
from app.agent.schemas import Itinerary
from app.auth import (
    Account,
    Credentials,
    Session,
    UsernameTaken,
    bearer_token,
    signed_in,
    viewer,
)
from app.auth import store as auth_store
from app.auth.store import (
    MAX_PASSWORD_CHARS,
    MIN_PASSWORD_CHARS,
    RESET_TTL,
    VERIFY_TTL,
)
from app.community import store as community
from app.community.store import (
    DEFAULT_FEED,
    Feed,
    PublishRequest,
    SharedPlan,
)
from app.config import settings
from app.map_page import day_map_page
from app.memory import store as memory_store
from app.memory.store import MAX_PREFERENCES_STORED
from app.ratelimit import Limit, limiter
from app.tools.maps import DEFAULT_LANGUAGE, MAX_MAP_PLACES, geocode_places, render_day_map

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)

# httpx logs every request line at INFO, URL and all -- and the Geocoding API only takes
# the Maps key as a query parameter, so it was being logged on every geocode call. Capped
# here rather than per call site, so any future query-string secret is covered too.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Report config problems at boot instead of on the first request.

    Warns rather than exits: `/health` and the offline test suite are meant to work
    without a key, and a container that refuses to start cannot be inspected. Requests
    that need the model still fail with a 500.
    """
    if settings.openai_api_key:
        logger.info("LLM configured: %s @ %s", settings.openai_model, settings.openai_base_url)
    else:
        logger.warning(
            "OPENAI_API_KEY is not set -- planning will fail with a 500 on the first "
            "request. Put a key in backend/.env."
        )

    if settings.smtp_host:
        logger.info("mail configured: %s:%s", settings.smtp_host, settings.smtp_port)
    else:
        logger.warning(
            "SMTP_HOST is not set -- password reset codes will be WRITTEN TO THIS LOG "
            "instead of emailed. Fine on a laptop; on a reachable server it means anyone "
            "who can read the logs can take over any account."
        )
    yield


def caller(request: Request) -> str:
    """A key for the client, for rate limiting only.

    `request.client.host`, **not** `X-Forwarded-For`: with no trusted proxy in front, that
    header is caller-supplied, so honouring it lets anyone reset their own limit by
    inventing an address. Put a proxy in front and this is the line to revisit.
    """
    return request.client.host if request.client else "unknown"


def enforce(key: str, limit: Limit) -> None:
    """Refuse with 429 if `key` has spent its allowance."""
    retry_after = limiter.check(key, limit)
    if retry_after is None:
        return
    logger.info("rate limited %s (%s)", key, limit.per_hour())
    raise HTTPException(
        status_code=429,
        detail=f"too many requests -- the limit is {limit.per_hour()}",
        # A number, so a client can wait exactly long enough instead of guessing or
        # retrying in a loop.
        headers={"Retry-After": str(max(1, round(retry_after)))},
    )


def enforce_plan_budget(key: str) -> None:
    """Both ceilings on a planning run: this caller's, and the service's.

    Checked and recorded together. One at a time, a run refused by the second would
    already have been counted against the first, so a caller bouncing off their own
    hourly limit would eat the day's global budget without producing a plan.

    The refusals differ on purpose. Spending your own allowance is a 429 -- you did it,
    waiting fixes it. Hitting the service ceiling is a 503: nothing is wrong with the
    request, and "too many requests" would send the caller retrying over something they
    cannot influence.
    """
    daily = ratelimits.plan_daily_global()
    personal = limiter.blocked(key, ratelimits.PLAN)
    if personal is not None:
        logger.info("rate limited %s (%s)", key, ratelimits.PLAN.per_hour())
        raise HTTPException(
            status_code=429,
            detail=f"too many requests -- the limit is {ratelimits.PLAN.per_hour()}",
            headers={"Retry-After": str(max(1, round(personal)))},
        )

    global_wait = limiter.blocked(GLOBAL_PLAN_KEY, daily)
    if global_wait is not None:
        logger.warning(
            "daily planning ceiling reached (%s runs); refusing until it rolls over",
            daily.count,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "this service has reached its daily planning limit "
                f"({daily.count} trips). Nothing is wrong with your request."
            ),
            headers={"Retry-After": str(max(1, round(global_wait)))},
        )

    limiter.record(key)
    limiter.record(GLOBAL_PLAN_KEY)


#: One key for every planning run, whoever makes it. Anonymous runs count too -- they
#: cost exactly as much.
GLOBAL_PLAN_KEY = "plan:all"


app = FastAPI(title=settings.app_name, lifespan=lifespan)


class SavedPreference(BaseModel):
    id: str
    text: str
    created_at: datetime


def preference_id(text: str) -> str:
    """Keep preference text out of URLs and access logs without a database migration."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@app.get("/preferences", response_model=list[SavedPreference])
async def list_preferences(
    response: Response, account: Annotated[Account, Depends(signed_in)]
) -> list[SavedPreference]:
    """All saved preferences for the signed-in account, including those beyond recall's cap."""
    response.headers["Cache-Control"] = "no-store"
    try:
        preferences = await memory_store.recall(account.id, limit=MAX_PREFERENCES_STORED)
    except sqlite3.Error as exc:
        logger.warning("preference listing failed: %s", type(exc).__name__)
        raise HTTPException(503, "Saved preferences are temporarily unavailable.") from exc
    return [
        SavedPreference(id=preference_id(p.text), text=p.text, created_at=p.created_at)
        for p in preferences
    ]


@app.delete("/preferences/{preference_key}", status_code=204)
async def delete_preference(
    preference_key: str, account: Annotated[Account, Depends(signed_in)]
) -> Response:
    """Idempotent deletion; another account's preference is indistinguishable from absence."""
    try:
        preferences = await memory_store.recall(account.id, limit=MAX_PREFERENCES_STORED)
        for preference in preferences:
            if preference_id(preference.text) == preference_key:
                await memory_store.forget_one(account.id, preference.text)
                break
    except sqlite3.Error as exc:
        logger.warning("preference deletion failed: %s", type(exc).__name__)
        raise HTTPException(503, "Could not delete this preference. Please try again.") from exc
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class HealthResponse(BaseModel):
    status: str
    app: str


class PlanRequest(BaseModel):
    """A trip described in free text, e.g. '3 days in Los Angeles next week, budget $900'."""

    message: str = Field(min_length=1, max_length=2000)

    # No user_id: whose preferences to recall and update comes from the bearer token, so
    # a caller cannot aim a memory write at another account. No token means an anonymous
    # run -- no recall, no writes.

    # ISO 4217 code the traveller settles up in. The plan is *estimated* in it, never
    # converted into it: conversion needs an FX source, and a stale rate makes an estimate
    # look precise. Empty lets the model use the destination's local currency.
    currency: str = Field(default="", pattern="^$|^[A-Za-z]{3}$")

    # An itinerary to **revise** rather than replace, sent by the client because this
    # service is stateless. A revision runs the same validate-repair-revalidate cycle as a
    # fresh plan, so an edit cannot quietly put the trip over budget. Null plans anew.
    previous: Itinerary | None = None


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check: confirm the service is up and config is readable."""
    return HealthResponse(status="ok", app=settings.app_name)


@app.post("/plan", response_model=PlanResult)
async def plan(
    payload: PlanRequest,
    request: Request,
    account: Annotated[Account | None, Depends(viewer)],
) -> PlanResult:
    """Plan a trip from a natural-language request.

    Signing in is optional: an anonymous run has no preferences to recall and writes none
    back. Upstream problems get their own status codes, so the client can tell a
    misconfigured server from a slow model from a dead one.
    """
    # Keyed on the account when there is one, so a shared address does not make one
    # traveller's runs count against another's.
    enforce_plan_budget(f"plan:{account.id if account else caller(request)}")
    try:
        return await plan_trip(
            payload.message,
            user_id=account.id if account else "",
            currency=payload.currency.upper(),
            previous=payload.previous,
        )
    except PlanningConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except PlanningTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except PlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/day-map", response_class=Response, responses={200: {"content": {"image/png": {}}}})
async def day_map(
    place: Annotated[list[str], Query(min_length=1, max_length=MAX_MAP_PLACES)],
    width: int = 640,
    height: int = 400,
) -> Response:
    """Render one day's stops as a PNG, numbered and joined in order.

    The client asks us rather than Google: the key never leaves the server, and the app
    needs no Play services -- which the test device lacks, so this is the only way a map
    reaches it at all.

    Repeat `place` once per stop, in visiting order:
    `/day-map?place=Willis Tower,Chicago&place=Fulton Market,Chicago`
    """
    result = await render_day_map(place, width, height)
    if not result.ok or result.image is None:
        # 502, not 500: the plan is fine, the picture of it is not.
        raise HTTPException(status_code=502, detail=result.error or "could not render the map")
    return Response(
        content=result.image,
        media_type="image/png",
        # Same stops always draw the same map, and it costs a paid request to find out.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/day-map/interactive", response_class=HTMLResponse)
async def day_map_interactive(
    place: Annotated[list[str], Query(min_length=1, max_length=MAX_MAP_PLACES)],
    language: str = DEFAULT_LANGUAGE,
    debug: bool = False,
) -> HTMLResponse:
    """The same day, as a map you can pan and zoom, for a WebView to load.

    The Android Maps SDK needs Play services; the JavaScript API needs only a Chromium,
    which is what makes an interactive map possible on this device at all.

    Unlike every other Google call here, the key **reaches the device** -- the API runs in
    the user's browser. Set `GOOGLE_MAPS_BROWSER_KEY` to a Maps-JavaScript-only key so a
    leak cannot be spent on Places or Routes. Stops are geocoded server-side, so the page
    itself needs no Geocoding rights.
    """
    key = settings.maps_browser_key
    if not key:
        # 503, not 500: the service is fine, this optional surface is switched off, and
        # the client falls back to the static image.
        raise HTTPException(status_code=503, detail="the interactive map is not configured")
    if not settings.google_maps_browser_key:
        logger.warning(
            "serving the interactive map with the server key; set GOOGLE_MAPS_BROWSER_KEY "
            "to a Maps-JavaScript-only key before this is exposed beyond a dev device"
        )

    points = await geocode_places(place, language)
    if not any(point.ok for point in points):
        first = next((point.error for point in points if point.error), None)
        raise HTTPException(status_code=502, detail=first or "no stop could be located")

    return HTMLResponse(
        # `debug=1` prints the failure reason into the page. The device's ROM suppresses
        # logcat, so on-screen is the only place a WebView failure can be read.
        content=day_map_page(points, key=key, language=language, debug=debug),
        # The page embeds a key, so it must not be cached by anything in between.
        headers={"Cache-Control": "no-store"},
    )


def _sse(event: PlanEvent) -> str:
    """Frame one event. `model_dump_json` emits a single line, which SSE requires."""
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


@app.post("/plan/stream")
async def plan_stream(
    payload: PlanRequest,
    request: Request,
    account: Annotated[Account | None, Depends(viewer)],
) -> StreamingResponse:
    """Plan a trip, streaming progress as Server-Sent Events.

    Failures arrive as a terminal `error` event rather than a status code: by the time
    a tool call fails the response has already started, so the status line is long
    gone. Clients must therefore treat `error` as fatal, not just log it.
    """

    # Same cost, same ceilings as `/plan`, and the same keys so a caller cannot get twice
    # the allowance by alternating between the two endpoints.
    enforce_plan_budget(f"plan:{account.id if account else caller(request)}")

    async def events() -> AsyncIterator[str]:
        try:
            async for event in stream_plan(
                payload.message,
                user_id=account.id if account else "",
                currency=payload.currency.upper(),
                previous=payload.previous,
            ):
                yield _sse(event)
        except PlanningError as exc:
            logger.info("stream failed: %s", exc)
            yield _sse(PlanEvent(type="error", message=str(exc)))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx and friends from buffering the stream into one lump.
            "X-Accel-Buffering": "no",
        },
    )


# --- community feed ----------------------------------------------------------------
#
# Publishing, browsing and saving other people's itineraries. Deliberately thin: this is
# CRUD over one table, and the interesting part of the product is upstream of it.
#
# Writing requires a token and authorship comes from it, never from the body. Reading does
# not. **Nothing here is moderated** -- see `docs/progress.md` before this is reachable by
# strangers.


class SaveRequest(BaseModel):
    #: No user_id: whose save this is comes from the token, or anyone could pad their own
    #: post's count with invented readers. False unsaves -- one endpoint rather than two,
    #: so the client's toggle is one call and both directions are idempotent.
    saved: bool = True


class SaveResponse(BaseModel):
    saved: bool
    save_count: int


@app.post("/community/plans", response_model=SharedPlan, status_code=201)
async def publish_plan(
    payload: PublishRequest,
    account: Annotated[Account, Depends(signed_in)],
) -> SharedPlan:
    """Share an itinerary to the community feed. Requires an account."""
    enforce(f"publish:{account.id}", ratelimits.PUBLISH)
    return await community.publish(payload, account.id, account.display_name)


@app.get("/community/plans", response_model=Feed)
async def community_feed(
    account: Annotated[Account | None, Depends(viewer)],
    limit: int = DEFAULT_FEED,
    cursor: str | None = None,
    destination: str = "",
) -> Feed:
    """One page of the feed, newest first.

    Readable without an account. A token fills in `saved_by_viewer`, which stays null
    without one -- "we do not know who is asking" is not "you have not saved this", and
    only one of them draws an empty heart.

    `cursor` comes from the previous page's `next_cursor`; null there means the end.
    Cursors, not an offset: the feed grows at the top, so one post published mid-read
    would shift every later page by one, showing a duplicate and hiding an item.

    `destination` is a case-insensitive substring match on the destination only.
    """
    try:
        return await community.feed(
            viewer_id=account.id if account else "",
            limit=limit,
            cursor=cursor,
            destination=destination,
        )
    except ValueError as exc:
        # A cursor this service did not issue. Refused rather than ignored: silently
        # starting from the top would show a client bug as an endless first page.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/community/plans/{plan_id}", response_model=SharedPlan)
async def community_plan(
    plan_id: str,
    account: Annotated[Account | None, Depends(viewer)],
) -> SharedPlan:
    """One shared plan, itinerary and all. Readable without an account."""
    plan = await community.get(plan_id, viewer_id=account.id if account else "")
    if plan is None:
        raise HTTPException(status_code=404, detail="that plan is no longer shared")
    return plan


@app.post("/community/plans/{plan_id}/save", response_model=SaveResponse)
async def save_shared_plan(
    plan_id: str,
    payload: SaveRequest,
    account: Annotated[Account, Depends(signed_in)],
) -> SaveResponse:
    """Save or unsave someone else's plan, returning the new count. Requires an account."""
    enforce(f"save:{account.id}", ratelimits.SAVE)
    outcome = await community.set_saved(plan_id, account.id, payload.saved)
    if outcome is None:
        raise HTTPException(status_code=404, detail="that plan is no longer shared")
    saved, count = outcome
    return SaveResponse(saved=saved, save_count=count)


@app.delete("/community/plans/{plan_id}", status_code=204)
async def withdraw_plan(
    plan_id: str,
    account: Annotated[Account, Depends(signed_in)],
) -> Response:
    """Take your own plan down.

    404 covers both "gone" and "not yours" on purpose -- the caller can do nothing
    different about either, and telling them apart would confirm that someone else's post
    exists at that id.
    """
    if not await community.withdraw(plan_id, account.id):
        raise HTTPException(status_code=404, detail="that plan is not yours, or is already gone")
    return Response(status_code=204)


# --- accounts ----------------------------------------------------------------------
#
# Registration hands back a token immediately: leaving the client to make a second call
# opens a window where the account exists and nobody can use it.
#
# The token is the only thing that establishes identity anywhere in this service. It goes
# in the `Authorization: Bearer` header, never in a query string -- query strings end up
# in access logs, exactly as the Maps key did (docs/decisions.md, 2026-08-17).


@app.post("/auth/register", response_model=Session, status_code=201)
async def register(credentials: Credentials, request: Request) -> Session:
    """Create an account and sign it in."""
    # Per address, because there is no account yet to key on. This is what makes the
    # per-author post cap mean anything: without it an attacker just registers more
    # authors.
    enforce(f"register:{caller(request)}", ratelimits.REGISTER_PER_ADDRESS)
    try:
        session = await auth_store.register(credentials)
    except UsernameTaken as exc:
        raise HTTPException(status_code=409, detail="that username is taken") from exc

    # Sent here rather than on a later request, because the address is only useful once
    # proven and the moment someone is most likely to have their inbox open is now.
    await send_verification(session.account.id, session.account.email)
    return session


@app.post("/auth/login", response_model=Session)
async def login(credentials: Credentials, request: Request) -> Session:
    """Start a session.

    One answer for an unknown user and a wrong password: telling them apart hands out a
    list of which accounts exist.

    Two limits, for two different attacks. Per-address catches password *spraying* -- one
    guess each against many accounts -- which a per-user counter never sees. Per-user
    counts **failures only**, or anyone could lock a stranger out by guessing wrong.
    """
    enforce(f"login:{caller(request)}", ratelimits.LOGIN_PER_ADDRESS)
    failures = f"login-fail:{credentials.username.lower()}"
    retry_after = limiter.blocked(failures, ratelimits.LOGIN_FAILURES_PER_USER)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many failed sign-ins for that account",
            headers={"Retry-After": str(max(1, round(retry_after)))},
        )

    session = await auth_store.login(credentials)
    if session is None:
        limiter.record(failures)
        raise HTTPException(status_code=401, detail="wrong username or password")
    # Remembering eventually should not leave someone counting against the limit.
    limiter.forget(failures)
    return session


@app.post("/auth/logout", status_code=204)
async def logout(authorization: Annotated[str | None, Header()] = None) -> Response:
    """End this session server-side, so a token copied off the device stops working.

    Always 204: a token that was not live needs nothing done about it, and saying so would
    distinguish a real token from a guess.
    """
    await auth_store.logout(bearer_token(authorization))
    return Response(status_code=204)


@app.get("/auth/me", response_model=Account)
async def me(account: Annotated[Account, Depends(signed_in)]) -> Account:
    """Who this token belongs to. The client's way to check a stored token still works."""
    return account


# --- password reset -----------------------------------------------------------------
#
# Two calls: ask for a code by email, then present the code with a new password. A code
# rather than a link because there is no web frontend to land a link on, and eight
# characters beat pasting a 43-character token into a phone.


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class ResetConfirm(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)


@app.post("/auth/reset/request", status_code=204)
async def request_reset(payload: ResetRequest, request: Request) -> Response:
    """Send a reset code, if that address belongs to an account.

    **Always 204.** Answering differently for a known address would turn this into a way
    to ask "does this person have an account here".
    """
    enforce(f"reset:{caller(request)}", ratelimits.RESET_PER_ADDRESS)

    outcome = await auth_store.start_reset(payload.email)
    if outcome is not None:
        account, code = outcome
        mail.mailer.send(
            to=account.email,
            subject=f"{settings.public_name} password reset",
            body=(
                f"Hello {account.display_name},\n\n"
                f"Your reset code is: {code}\n\n"
                f"It works once and expires in {round(RESET_TTL.total_seconds() / 60)} "
                "minutes. Signing in again elsewhere will not be possible afterwards -- "
                "resetting your password ends every session, including any that is not "
                "yours.\n\n"
                "If you did not ask for this, nothing has happened to your account and "
                "you can ignore this message.\n"
            ),
        )
    return Response(status_code=204)


@app.post("/auth/reset/confirm", status_code=204)
async def confirm_reset(payload: ResetConfirm, request: Request) -> Response:
    """Set a new password from a reset code.

    Rate limited per address, because that -- with the code's own entropy -- is what
    bounds guessing. There is no per-code attempt counter: a wrong guess matches no
    stored code, so there would be nothing to count it against.
    """
    enforce(f"reset-confirm:{caller(request)}", ratelimits.RESET_CONFIRM_PER_ADDRESS)

    if not await auth_store.complete_reset(payload.code, payload.password):
        raise HTTPException(status_code=400, detail="that code is not valid, or it has expired")
    return Response(status_code=204)


# --- proving the address ------------------------------------------------------------
#
# **Password reset only works for a proven address.** Without that rule an unverified
# address is a takeover route: register with a stranger's address, and they can reset
# their way in.


class EmailChange(BaseModel):
    #: Blank clears the address, which is how someone opts out of being recoverable.
    email: str = Field(default="", max_length=254)


class VerifyConfirm(BaseModel):
    code: str = Field(min_length=1, max_length=32)


async def send_verification(account_id: str, email: str) -> None:
    """Mail a verification code, if there is an address to prove."""
    code = await auth_store.start_verification(account_id, email)
    if code is None:
        return
    mail.mailer.send(
        to=email,
        subject=f"Confirm your {settings.public_name} email",
        body=(
            f"Your confirmation code is: {code}\n\n"
            f"It expires in {round(VERIFY_TTL.total_seconds() / 3600)} hours. Until this "
            "address is confirmed it cannot be used to reset your password.\n\n"
            "If you did not create an account, someone typed this address by mistake. "
            "Ignoring this message leaves it unusable, which is the outcome you want.\n"
        ),
    )


@app.post("/auth/email", response_model=Account)
async def change_email(
    payload: EmailChange,
    account: Annotated[Account, Depends(signed_in)],
    request: Request,
) -> Account:
    """Set or change this account's address, and send a code to prove it.

    The new address always starts unverified. Carrying the flag across a change would let
    anyone holding a session move a verified account onto an address they do not own.
    """
    enforce(f"email-change:{account.id}", ratelimits.RESET_PER_ADDRESS)

    if not await auth_store.set_email(account.id, payload.email):
        raise HTTPException(status_code=409, detail="another account already uses that address")
    await send_verification(account.id, payload.email.strip().lower())

    updated = await auth_store.resolve(bearer_token(request.headers.get("authorization")))
    return updated or account


@app.post("/auth/verify/request", status_code=204)
async def resend_verification(
    account: Annotated[Account, Depends(signed_in)],
) -> Response:
    """Send another code for this account's address.

    Takes a session rather than an address, so it cannot become the enumeration oracle the
    reset endpoint carefully is not.
    """
    enforce(f"verify-send:{account.id}", ratelimits.RESET_PER_ADDRESS)
    await send_verification(account.id, account.email)
    return Response(status_code=204)


@app.post("/auth/verify", status_code=204)
async def confirm_verification(
    payload: VerifyConfirm,
    account: Annotated[Account, Depends(signed_in)],
    request: Request,
) -> Response:
    """Prove the address with a code from the email."""
    enforce(f"verify:{caller(request)}", ratelimits.RESET_CONFIRM_PER_ADDRESS)

    if not await auth_store.complete_verification(account.id, payload.code):
        raise HTTPException(status_code=400, detail="that code is not valid, or it has expired")
    return Response(status_code=204)
