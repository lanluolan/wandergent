"""Server-side accounts and bearer tokens.

Until now every `user_id` in this service was a *claim the client made* -- fine while the
only client was a phone on the other end of `adb reverse`, and worthless the moment two
people can reach the same server, because anyone could post as anyone. This module is what
turns those claims into proof.

**Storage is SQLite via the standard library**, in a worker thread, the same as
`memory/store.py` and `community/store.py`. Its own file, so it can move to PostgreSQL on
its own schedule.

**Passwords: PBKDF2-HMAC-SHA256 from `hashlib`.** The Android client hashes on-device with
a single round of SHA-256, which is adequate for a local unlock and *not* adequate for a
server-side password store -- one fast hash is billions of guesses a second on a GPU.
Argon2id would be better still, but it is a C extension dependency, and PBKDF2 with a real
iteration count is what the standard library offers. That trade is the same one this
project already made choosing `sqlite3` over SQLAlchemy.

**Tokens: opaque random strings, stored hashed.** Deliberately not JWT, which is what
`docs/progress.md` sketched:

- **Revocable.** Logging out deletes a row. A JWT needs a denylist table to be revocable,
  which means carrying both the state *and* the signature verification.
- **Nothing to get wrong.** No `alg: none`, no HS/RS confusion, no library.
- JWT's one advantage -- no database lookup -- buys nothing here. One server, and every
  request already touches SQLite.

The token is stored as a SHA-256 digest so a leaked database does not hand over live
sessions. A single fast hash is right *here* and wrong for passwords: the token is 256
bits of `secrets` output, so there is no guessing attack to slow down.
"""

import asyncio
import hashlib
import logging
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: OWASP's floor for PBKDF2-HMAC-SHA256. Stored per account rather than assumed, so the
#: cost can be raised for new accounts without locking existing ones out -- an old row
#: keeps verifying at its own count until its owner next logs in.
PBKDF2_ITERATIONS = 600_000

#: How long a session lasts. Long enough that a travel app does not ask every week, short
#: enough that a token copied off a lost phone stops working.
TOKEN_TTL = timedelta(days=30)

MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_CHARS = 128

_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    iterations    INTEGER NOT NULL,
    created_at    TEXT NOT NULL
)
""",
    # Uniqueness enforced by the database, not by a check-then-insert: two registrations
    # racing on the same name would both pass the check.
    "CREATE UNIQUE INDEX IF NOT EXISTS accounts_username ON accounts (username)",
    """
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS tokens_account ON tokens (account_id)",
    """
CREATE TABLE IF NOT EXISTS reset_codes (
    code_hash  TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS reset_codes_account ON reset_codes (account_id)",
    # A separate table rather than a `purpose` column on `reset_codes`. One table would
    # mean every lookup had to remember to filter by purpose, and the day one forgets is
    # the day a code mailed to prove an address also sets a password. Two tables make
    # that mistake unrepresentable rather than merely discouraged.
    """
CREATE TABLE IF NOT EXISTS verify_codes (
    code_hash  TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS verify_codes_account ON verify_codes (account_id)",
)

#: Added after accounts already existed, so it cannot go in the CREATE above -- that runs
#: `IF NOT EXISTS` and would leave an existing table untouched. Applied idempotently.
_MIGRATIONS = (
    "ALTER TABLE accounts ADD COLUMN email TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE accounts ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
)

#: How long a verification code is good for. Longer than a reset code: nothing is at
#: stake if it sits unused, and someone registering on a phone may not reach their mail
#: until the evening.
VERIFY_TTL = timedelta(hours=24)

#: How long a reset code is good for. Short, because it is a bearer credential sitting in
#: an inbox: long enough to walk to a laptop, not long enough to matter if the inbox is
#: read next month.
RESET_TTL = timedelta(hours=1)

#: What stops a code being guessed: its own entropy, and the endpoint's rate limit.
#:
#: Not a per-code attempt counter, which was written here first and then removed -- it
#: cannot work. A wrong guess hashes to no stored row, so there is nothing to charge the
#: attempt against. It would have looked like a defence and counted nothing. Eight
#: characters from a 32-symbol alphabet is about 40 bits, and `/auth/reset/confirm` is
#: rate limited per address.

#: No 0/O or 1/I: the recipient reads this off a screen and types it into a phone, and a
#: character pair nobody can tell apart turns a working code into a support request.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class Account(BaseModel):
    """Who someone is. Never carries anything password-shaped."""

    id: str
    username: str
    display_name: str
    created_at: datetime
    #: Where a reset code can be sent. Blank means the account cannot be recovered -- see
    #: `Credentials.email` for why it is optional rather than required.
    email: str = ""
    #: Whether that address has been proven to belong to whoever holds this account.
    #:
    #: **Reset requires it.** Without that rule verification buys nothing and, worse, an
    #: unverified address is a takeover route: register with a stranger's address by
    #: typo or on purpose, and the stranger can reset their way into the account.
    email_verified: bool = False


class Session(BaseModel):
    """A fresh login. `token` is shown exactly once -- only its hash is stored."""

    token: str
    expires_at: datetime
    account: Account


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=MIN_PASSWORD_CHARS, max_length=MAX_PASSWORD_CHARS)
    #: Only read on registration; ignored on login. Blank falls back to the username.
    display_name: str = Field(default="", max_length=64)

    #: Also registration-only. **Optional, and that is a deliberate trade.** Requiring it
    #: would lock out the accounts that already exist without one, and an address nobody
    #: verified is not proof of anything anyway. What it buys is the *possibility* of
    #: recovery; without one, a forgotten password ends the account. The client says so.
    email: str = Field(default="", max_length=254)


class UsernameTaken(Exception):
    """Registration lost the race, or the name was already in use."""


def _hash_password(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthStore:
    """Accounts and sessions. Every method is safe to call concurrently."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._init_lock = asyncio.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return

            def create() -> None:
                with self._connect() as connection:
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    for statement in _MIGRATIONS:
                        try:
                            connection.execute(statement)
                        except sqlite3.OperationalError:
                            # Already applied. SQLite has no `ADD COLUMN IF NOT EXISTS`,
                            # and reading the table schema back to decide would be more
                            # code for the same outcome.
                            pass

            await asyncio.to_thread(create)
            self._ready = True

    async def register(self, credentials: Credentials) -> Session:
        """Create an account and sign it in. Raises `UsernameTaken`.

        Signing in immediately is deliberate: a register that leaves the client to make a
        second call has a window where the account exists and nobody can use it, and the
        client has to handle a half-finished state that carries no useful information.
        """
        await self._ensure_schema()
        username = credentials.username.lower()
        salt = secrets.token_bytes(16).hex()
        account = Account(
            id=str(uuid.uuid4()),
            username=username,
            display_name=credentials.display_name.strip() or credentials.username,
            created_at=datetime.now(UTC),
            email=credentials.email.strip().lower(),
        )
        digest = await asyncio.to_thread(
            _hash_password, credentials.password, salt, PBKDF2_ITERATIONS
        )

        def insert() -> None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO accounts (id, username, display_name, password_salt,"
                    " password_hash, iterations, created_at, email)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        account.id,
                        account.username,
                        account.display_name,
                        salt,
                        digest,
                        PBKDF2_ITERATIONS,
                        account.created_at.isoformat(),
                        account.email,
                    ),
                )

        try:
            await asyncio.to_thread(insert)
        except sqlite3.IntegrityError as exc:
            raise UsernameTaken(credentials.username) from exc

        logger.info("registered account %s (%s)", account.id, account.username)
        return await self._issue(account)

    async def login(self, credentials: Credentials) -> Session | None:
        """Verify a password and start a session. None means "no", without saying why.

        Deliberately one answer for an unknown user and a wrong password: telling them
        apart hands out a list of which accounts exist.
        """
        await self._ensure_schema()
        username = credentials.username.lower()

        def query() -> tuple | None:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT id, username, display_name, password_salt, password_hash,"
                    " iterations, created_at, email, email_verified"
                    " FROM accounts WHERE username = ?",
                    (username,),
                ).fetchone()

        row = await asyncio.to_thread(query)
        if row is None:
            # Spend the same work anyway, so "no such user" is not measurably faster than
            # "wrong password". Cheap here, and the alternative is a timing oracle.
            await asyncio.to_thread(
                _hash_password,
                credentials.password,
                secrets.token_bytes(16).hex(),
                PBKDF2_ITERATIONS,
            )
            return None

        digest = await asyncio.to_thread(_hash_password, credentials.password, row[3], int(row[5]))
        if not secrets.compare_digest(digest, row[4]):
            return None

        account = Account(
            id=row[0],
            username=row[1],
            display_name=row[2],
            created_at=datetime.fromisoformat(row[6]),
            email=row[7],
            email_verified=bool(row[8]),
        )
        return await self._issue(account)

    async def _issue(self, account: Account) -> Session:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + TOKEN_TTL

        def insert() -> None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO tokens (token_hash, account_id, created_at, expires_at)"
                    " VALUES (?,?,?,?)",
                    (
                        _hash_token(token),
                        account.id,
                        datetime.now(UTC).isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                # Housekeeping on the way past: expired rows are dead weight and nothing
                # else would ever come along to remove them.
                connection.execute(
                    "DELETE FROM tokens WHERE expires_at < ?", (datetime.now(UTC).isoformat(),)
                )

        await asyncio.to_thread(insert)
        return Session(token=token, expires_at=expires_at, account=account)

    async def resolve(self, token: str) -> Account | None:
        """The account a bearer token belongs to, or None if it is not a live session."""
        if not token:
            return None
        await self._ensure_schema()

        def query() -> tuple | None:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT a.id, a.username, a.display_name, a.created_at, t.expires_at,"
                    " a.email, a.email_verified"
                    " FROM tokens t JOIN accounts a ON a.id = t.account_id"
                    " WHERE t.token_hash = ?",
                    (_hash_token(token),),
                ).fetchone()

        row = await asyncio.to_thread(query)
        if row is None:
            return None
        if datetime.fromisoformat(row[4]) <= datetime.now(UTC):
            # Expiry is checked here rather than only pruned on login: a token whose row
            # has not been swept yet must still stop working the moment it lapses.
            return None
        return Account(
            id=row[0],
            username=row[1],
            display_name=row[2],
            created_at=datetime.fromisoformat(row[3]),
            email=row[5],
            email_verified=bool(row[6]),
        )

    # --- proving the address ---------------------------------------------------------

    async def start_verification(self, account_id: str, email: str) -> str | None:
        """Issue a verification code for `email`, or None if there is nothing to verify.

        Takes the address explicitly rather than reading it back, so setting a new address
        and proving it are one operation from the caller's side and cannot drift apart.
        """
        await self._ensure_schema()
        address = email.strip().lower()
        if not address:
            return None

        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))

        def insert() -> None:
            with self._connect() as connection:
                # One outstanding code per account, same reasoning as reset: someone who
                # asks twice should find the newest works rather than discovering later
                # which of two was meant.
                connection.execute("DELETE FROM verify_codes WHERE account_id = ?", (account_id,))
                connection.execute(
                    "INSERT INTO verify_codes (code_hash, account_id, email, created_at,"
                    " expires_at) VALUES (?,?,?,?,?)",
                    (
                        _hash_token(code),
                        account_id,
                        address,
                        datetime.now(UTC).isoformat(),
                        (datetime.now(UTC) + VERIFY_TTL).isoformat(),
                    ),
                )
                connection.execute(
                    "DELETE FROM verify_codes WHERE expires_at < ?",
                    (datetime.now(UTC).isoformat(),),
                )

        await asyncio.to_thread(insert)
        return code

    async def complete_verification(self, account_id: str, code: str) -> bool:
        """Mark the address proven. False if the code is not this account's, or lapsed.

        Scoped to the account making the request -- a code is not a bearer credential for
        somebody else's address, and checking the code alone would make it one.

        **The address is taken from the code, not from the account row.** Between issuing
        and confirming, the account may have been pointed at a different address; marking
        *that* one verified on the strength of a code mailed elsewhere is precisely the
        takeover this feature exists to prevent.
        """
        await self._ensure_schema()
        if not code.strip():
            return False
        digest = _hash_token(code.strip().upper())

        def apply() -> bool:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT email, expires_at FROM verify_codes"
                    " WHERE code_hash = ? AND account_id = ?",
                    (digest, account_id),
                ).fetchone()
                if row is None:
                    return False
                email, expires_at = row[0], row[1]
                if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
                    connection.execute("DELETE FROM verify_codes WHERE code_hash = ?", (digest,))
                    return False
                # Only if the account still points at the address the code was sent to.
                changed = connection.execute(
                    "UPDATE accounts SET email_verified = 1 WHERE id = ? AND email = ?",
                    (account_id, email),
                ).rowcount
                connection.execute("DELETE FROM verify_codes WHERE account_id = ?", (account_id,))
                return bool(changed)

        verified = await asyncio.to_thread(apply)
        if verified:
            logger.info("email verified for account %s", account_id)
        return verified

    async def set_email(self, account_id: str, email: str) -> bool:
        """Point an account at a new address, unverified. False if it is already taken.

        Marking it unverified is the whole point: an address is only proven for as long
        as it is the one that was proven. Changing it and keeping the flag would let
        anyone with a session move a verified account onto an address they do not own.
        """
        await self._ensure_schema()
        address = email.strip().lower()

        def update() -> bool:
            with self._connect() as connection:
                clash = connection.execute(
                    "SELECT 1 FROM accounts WHERE email = ? AND email != '' AND id != ?",
                    (address, account_id),
                ).fetchone()
                if address and clash is not None:
                    # Two accounts on one address means one reset request with two
                    # possible answers.
                    return False
                connection.execute(
                    "UPDATE accounts SET email = ?, email_verified = 0 WHERE id = ?",
                    (address, account_id),
                )
                connection.execute("DELETE FROM verify_codes WHERE account_id = ?", (account_id,))
                return True

        return await asyncio.to_thread(update)

    # --- password reset ------------------------------------------------------------

    async def start_reset(self, email: str) -> tuple[Account, str] | None:
        """Issue a reset code for the account at `email`, or None if there is none.

        The caller must answer identically either way -- a reset endpoint that behaves
        differently for a known address is an account-enumeration oracle wearing a
        helpful face.

        Any code already outstanding for the account is destroyed. Someone who asks twice
        because the first mail was slow should find that the newest code is the one that
        works, rather than discovering later which of two they were meant to use.
        """
        await self._ensure_schema()
        address = email.strip().lower()
        if not address:
            return None

        def query() -> tuple | None:
            with self._connect() as connection:
                return connection.execute(
                    # `email_verified = 1` is the load-bearing clause. An unverified
                    # address is a takeover route: whoever really owns it could reset
                    # their way into an account that merely typed it.
                    "SELECT id, username, display_name, created_at, email"
                    " FROM accounts WHERE email = ? AND email != '' AND email_verified = 1",
                    (address,),
                ).fetchone()

        row = await asyncio.to_thread(query)
        if row is None:
            return None

        account = Account(
            id=row[0],
            username=row[1],
            display_name=row[2],
            created_at=datetime.fromisoformat(row[3]),
            email=row[4],
            email_verified=True,
        )
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))
        expires_at = datetime.now(UTC) + RESET_TTL

        def insert() -> None:
            with self._connect() as connection:
                connection.execute("DELETE FROM reset_codes WHERE account_id = ?", (account.id,))
                connection.execute(
                    "INSERT INTO reset_codes (code_hash, account_id, created_at, expires_at)"
                    " VALUES (?,?,?,?)",
                    (
                        _hash_token(code),
                        account.id,
                        datetime.now(UTC).isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                connection.execute(
                    "DELETE FROM reset_codes WHERE expires_at < ?",
                    (datetime.now(UTC).isoformat(),),
                )

        await asyncio.to_thread(insert)
        logger.info("reset code issued for account %s", account.id)
        return account, code

    async def complete_reset(self, code: str, new_password: str) -> bool:
        """Set a new password from a reset code. False if the code is not usable.

        **Every session is revoked.** If the reset happened because the account was
        compromised, leaving the attacker's bearer token alive would make the whole
        exercise pointless -- and that is the case the feature exists for.
        """
        await self._ensure_schema()
        if not code.strip():
            return False
        digest = _hash_token(code.strip().upper())

        def find() -> tuple | None:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT account_id, expires_at FROM reset_codes WHERE code_hash = ?",
                    (digest,),
                ).fetchone()

        row = await asyncio.to_thread(find)
        if row is None:
            return False

        account_id, expires_at = row[0], row[1]
        if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
            # Checked here, not only swept on the next request: a lapsed code must stop
            # working the moment it lapses, whatever else has or has not run since.
            await asyncio.to_thread(self._drop_code, digest)
            return False

        salt = secrets.token_bytes(16).hex()
        password_hash = await asyncio.to_thread(
            _hash_password, new_password, salt, PBKDF2_ITERATIONS
        )

        def apply() -> None:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE accounts SET password_salt = ?, password_hash = ?,"
                    " iterations = ? WHERE id = ?",
                    (salt, password_hash, PBKDF2_ITERATIONS, account_id),
                )
                connection.execute("DELETE FROM reset_codes WHERE account_id = ?", (account_id,))
                connection.execute("DELETE FROM tokens WHERE account_id = ?", (account_id,))

        await asyncio.to_thread(apply)
        logger.info("password reset for account %s; all sessions revoked", account_id)
        return True

    def _drop_code(self, code_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM reset_codes WHERE code_hash = ?", (code_hash,))

    async def logout(self, token: str) -> None:
        """End one session. Silent about tokens that were not live -- there is nothing
        for the caller to do differently, and saying so distinguishes a real token from
        a guess."""
        if not token:
            return
        await self._ensure_schema()

        def delete() -> None:
            with self._connect() as connection:
                connection.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash_token(token),))

        await asyncio.to_thread(delete)


__all__ = [
    "Account",
    "AuthStore",
    "Credentials",
    "Session",
    "UsernameTaken",
]
