"""Authentication: the process-wide store and the request dependencies.

Two dependencies, and the difference between them is the whole authorization model:

- [`viewer`] resolves a token if one was sent and yields None otherwise. Reading the feed
  and planning a trip work without an account, exactly as they did before -- an anonymous
  caller simply has no saved-state and no remembered preferences.
- [`signed_in`] refuses without a live token. Everything that writes something other
  people see goes through it.

Neither reads an id from the request body, and the bodies no longer have one. That is the
actual fix: while the field existed, any check on it was a formality.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.auth.store import Account, AuthStore, Credentials, Session, UsernameTaken
from app.config import settings

store = AuthStore(settings.auth_db_path)


def bearer_token(authorization: str | None) -> str:
    """The token out of an Authorization header, or "" if there is not one.

    Tolerant of case and spacing because clients differ, and strict about the scheme:
    a bare token with no `Bearer` prefix is a misconfigured client, not a session.
    """
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def viewer(
    authorization: Annotated[str | None, Header()] = None,
) -> Account | None:
    """Who is asking, if anyone. Never raises -- absent and invalid both mean anonymous."""
    return await store.resolve(bearer_token(authorization))


async def signed_in(
    account: Annotated[Account | None, Depends(viewer)],
) -> Account:
    """Who is asking. 401 when that cannot be established."""
    if account is None:
        raise HTTPException(
            status_code=401,
            detail="sign in first",
            # Named so a client can tell a lapsed session from a missing one and react by
            # signing out rather than by retrying forever.
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account


__all__ = [
    "Account",
    "bearer_token",
    "AuthStore",
    "Credentials",
    "Session",
    "UsernameTaken",
    "signed_in",
    "store",
    "viewer",
]
