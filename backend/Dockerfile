# syntax=docker/dockerfile:1
#
# Two stages: uv resolves and installs into a virtualenv, the runtime image gets only the
# venv and the source. The builder carries uv and a package cache that the final image has
# no use for, and an image that ships its own build tools is an image with a larger attack
# surface for no benefit.

FROM python:3.13-slim AS builder

# Pinned rather than `latest`: a floating tag makes the same commit produce different
# images on different days, which is the one thing a build is supposed to rule out.
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Matches `python-downloads = "never"` in pyproject: use the interpreter this image
    # already has rather than silently fetching a second one.
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the two files that decide the dependency graph, so editing application code does
# not invalidate the layer that installs several hundred megabytes of packages.
COPY pyproject.toml uv.lock ./

# `--locked` fails rather than re-resolving: a container that quietly upgraded a
# dependency is a container that does not match what the tests ran against.
# `--no-dev` leaves pytest and ruff out of the runtime image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project


FROM python:3.13-slim AS runtime

# Nothing here needs root. If the process is ever exploited, this is the difference
# between reading /data and owning the host.
RUN useradd --create-home --uid 10001 wandergent

WORKDIR /app

COPY --from=builder --chown=wandergent:wandergent /app/.venv /app/.venv
COPY --chown=wandergent:wandergent app ./app

# Where the three SQLite files live. Created here with the right owner so a named volume
# mounted over it inherits that ownership -- Docker copies the image's directory metadata
# on first mount, and without this the process cannot write to its own database.
RUN mkdir -p /data && chown wandergent:wandergent /data
VOLUME ["/data"]

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Absolute, and on the volume. The defaults are relative, so they would land in the
    # container's writable layer and vanish with the container -- taking every account,
    # session and shared trip with them, silently.
    MEMORY_DB_PATH=/data/wandergent-memory.db \
    COMMUNITY_DB_PATH=/data/wandergent-community.db \
    AUTH_DB_PATH=/data/wandergent-auth.db

USER wandergent
EXPOSE 8000

# No curl in a slim image, and adding one to run a health check is a package more than the
# job needs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# **One worker, deliberately.** Two would break two things at once: the rate limiter keeps
# its counters in process memory, so N workers means N times every limit; and several
# processes writing the same SQLite file contend on a lock that WAL only partly relieves.
# Both are fixed by the same Phase 4 work -- Redis and PostgreSQL -- and until then the
# honest configuration is a single process. See `app/ratelimit.py`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
