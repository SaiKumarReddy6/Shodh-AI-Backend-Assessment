from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Generator

import docker
from docker.errors import DockerException, NotFound

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    or_,
    select,
    text,
    update,
)

from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


# ============================================================
# CONFIGURATION
# ============================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./shodh.db",
)

connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {"connect_timeout": 5}
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("shodh")


# ============================================================
# JUDGE CONFIGURATION
# ============================================================

DOCKER_IMAGE = os.getenv(
    "JUDGE_IMAGE",
    "python:3.12-alpine",
)

MAX_SOURCE_BYTES = 64_000

MAX_OUTPUT_BYTES = 64_000

WALL_SECONDS = 3

MAX_ATTEMPTS = 3

# A live worker refreshes its heartbeat every 10 seconds.
HEARTBEAT_INTERVAL_SECONDS = 10

# A job is considered abandoned only after this interval.
RECOVERY_STALE_SECONDS = 60

# Worker periodically checks for abandoned submissions.
RECOVERY_INTERVAL_SECONDS = 15


# ============================================================
# DEVELOPMENT TOKENS
# ============================================================

DEV_TOKENS = {
    "organizer-a": (
        "organizer-a",
        "org-a",
        "organizer",
    ),

    "student-a": (
        "student-a",
        "org-a",
        "participant",
    ),

    "organizer-b": (
        "organizer-b",
        "org-b",
        "organizer",
    ),

    "student-b": (
        "student-b",
        "org-b",
        "participant",
    ),
}


security = HTTPBearer(
    auto_error=False,
)


# ============================================================
# DATABASE MODELS
# ============================================================

class Base(DeclarativeBase):
    pass


class Contest(Base):

    __tablename__ = "contests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    organization_id: Mapped[str] = mapped_column(
        String(80),
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(160),
    )

    opens_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )

    closes_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )


class ContestParticipant(Base):

    __tablename__ = "contest_participants"

    contest_id: Mapped[str] = mapped_column(
        ForeignKey("contests.id"),
        primary_key=True,
    )

    actor_id: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Problem(Base):

    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    contest_id: Mapped[str] = mapped_column(
        ForeignKey("contests.id"),
        index=True,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
    )

    title: Mapped[str] = mapped_column(
        String(160),
    )

    tests: Mapped[list[Any]] = mapped_column(
        JSON,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )


class Submission(Base):

    __tablename__ = "submissions"

    __table_args__ = (
        UniqueConstraint(
            "contest_id",
            "actor_id",
            "idempotency_key",
            name="uq_submission_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    contest_id: Mapped[str] = mapped_column(
        ForeignKey("contests.id"),
        index=True,
    )

    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id"),
        index=True,
    )

    actor_id: Mapped[str] = mapped_column(
        String(80),
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(128),
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
    )

    source: Mapped[str] = mapped_column(
        Text,
    )

    problem_version: Mapped[int] = mapped_column(
        Integer,
    )

    test_snapshot: Mapped[list[Any]] = mapped_column(
        JSON,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="queued",
        index=True,
    )

    verdict: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    score: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    active_attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    attempts_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class JudgeAttempt(Base):

    __tablename__ = "judge_attempts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id"),
        index=True,
    )

    number: Mapped[int] = mapped_column(
        Integer,
    )

    problem_version: Mapped[int] = mapped_column(
        Integer,
    )

    verdict: Mapped[str] = mapped_column(
        String(32),
        default="running",
    )

    mode: Mapped[str] = mapped_column(
        String(20),
        default="docker",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    detail: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
    )

    # Added for worker lease/heartbeat recovery.
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# ============================================================
# REQUEST MODELS
# ============================================================

class ContestCreate(BaseModel):

    title: str = Field(
        min_length=1,
        max_length=160,
    )

    opens_in_minutes: int = Field(
        default=0,
        ge=0,
        le=30 * 24 * 60,
    )

    duration_minutes: int = Field(
        default=60,
        ge=5,
        le=365 * 24 * 60,
    )


class ProblemCreate(BaseModel):

    title: str = Field(
        min_length=1,
        max_length=160,
    )

    tests: list[dict[str, Any]] = Field(
        min_length=1,
    )

    active: bool = True


class ProblemUpdate(BaseModel):

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )

    tests: list[dict[str, Any]] | None = Field(
        default=None,
        min_length=1,
    )

    active: bool | None = None


class SubmissionRequest(BaseModel):

    problem_id: str

    language: str = "python"

    source: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_BYTES,
    )

    @field_validator("source")
    @classmethod
    def validate_source_size(
        cls,
        value: str,
    ) -> str:

        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:

            raise ValueError(
                "Source code exceeds maximum size"
            )

        return value


# ============================================================
# WORKER STATE
# ============================================================

_worker_stop = threading.Event()

_worker_thread: threading.Thread | None = None


# ============================================================
# UTILITIES
# ============================================================

def utcnow() -> datetime:

    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:

    if value.tzinfo is None:

        return value.replace(
            tzinfo=timezone.utc,
        )

    return value.astimezone(
        timezone.utc,
    )


def db_session() -> Generator[Session, None, None]:

    session = SessionLocal()

    try:

        yield session

    finally:

        session.close()


# ============================================================
# DATABASE COMPATIBILITY MIGRATION
# ============================================================

def ensure_schema_compatibility() -> None:
    """
    Adds heartbeat_at to existing judge_attempts tables.

    create_all() creates missing tables but does not add new
    columns to existing tables. This small migration handles
    databases created by the previous version of this backend.
    """

    if "judge_attempts" not in inspect(engine).get_table_names():
        return

    try:

        with engine.begin() as connection:

            columns = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "judge_attempts"
                )
            }

            if "heartbeat_at" in columns:
                return

            if engine.dialect.name == "postgresql":

                column_type = "TIMESTAMP WITH TIME ZONE"

            else:

                column_type = "DATETIME"

            connection.exec_driver_sql(
                "ALTER TABLE judge_attempts "
                f"ADD COLUMN heartbeat_at {column_type} NULL"
            )

        logger.info(
            "Database compatibility migration completed"
        )

    except SQLAlchemyError:

        # Another application instance may have added the
        # column concurrently. Ignore the error only when
        # the column is now present.
        columns = {
            column["name"]
            for column in inspect(engine).get_columns(
                "judge_attempts"
            )
        }

        if "heartbeat_at" not in columns:
            raise

        logger.info(
            "Heartbeat column already exists"
        )


# ============================================================
# AUTHENTICATION
# ============================================================

def identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> tuple[str, str, str]:

    if credentials is None:

        raise HTTPException(
            status_code=401,
            detail="Missing authorization token",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    token = credentials.credentials.strip()

    user = DEV_TOKENS.get(token)

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid development credential",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    return user


def contest_for(
    session: Session,
    contest_id: str,
    user: tuple[str, str, str],
) -> Contest:

    contest = session.get(
        Contest,
        contest_id,
    )

    if (
        not contest
        or contest.organization_id != user[1]
    ):

        raise HTTPException(
            status_code=404,
            detail="Contest not found",
        )

    return contest


def participant(
    user: tuple[str, str, str],
) -> None:

    if user[2] != "participant":

        raise HTTPException(
            status_code=403,
            detail="Participant role required",
        )


def organizer(
    user: tuple[str, str, str],
) -> None:

    if user[2] != "organizer":

        raise HTTPException(
            status_code=403,
            detail="Organizer role required",
        )


# ============================================================
# TEST CASE VALIDATION
# ============================================================

def _normalize_test_cases(
    tests: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    normalized: list[dict[str, Any]] = []

    for case in tests:

        if not isinstance(case, dict):
            continue

        normalized.append(
            {
                "input": str(
                    case.get("input", "")
                ),

                "expected": str(
                    case.get("expected", "")
                ),

                "hidden": bool(
                    case.get("hidden", False)
                ),
            }
        )

    if not normalized:

        raise HTTPException(
            status_code=422,
            detail="At least one valid test case is required",
        )

    return normalized


# ============================================================
# DATABASE SEEDING
# ============================================================

def seed() -> None:

    with SessionLocal.begin() as session:

        existing = session.scalar(
            select(Contest.id).limit(1)
        )

        if existing:
            return

        now = utcnow()

        contest_a = Contest(
            id=str(uuid.uuid4()),
            organization_id="org-a",
            title="Demo Contest A",
            opens_at=now,
            closes_at=now + timedelta(days=365),
        )

        contest_b = Contest(
            id=str(uuid.uuid4()),
            organization_id="org-b",
            title="Demo Contest B",
            opens_at=now,
            closes_at=now + timedelta(days=365),
        )

        session.add_all(
            [
                contest_a,
                contest_b,
            ]
        )

        session.flush()

        session.add_all(
            [
                Problem(
                    id="sum-a",
                    contest_id=contest_a.id,
                    title="Add two integers",
                    version=1,
                    tests=[
                        {
                            "input": "2 3\n",
                            "expected": "5\n",
                            "hidden": False,
                        },
                        {
                            "input": "-1 4\n",
                            "expected": "3\n",
                            "hidden": True,
                        },
                    ],
                    active=True,
                ),

                Problem(
                    id="echo-a",
                    contest_id=contest_a.id,
                    title="Echo input",
                    version=1,
                    tests=[
                        {
                            "input": "hello\n",
                            "expected": "hello\n",
                            "hidden": False,
                        }
                    ],
                    active=True,
                ),

                Problem(
                    id="sum-b",
                    contest_id=contest_b.id,
                    title="Add two integers",
                    version=1,
                    tests=[
                        {
                            "input": "7 8\n",
                            "expected": "15\n",
                            "hidden": True,
                        }
                    ],
                    active=True,
                ),
            ]
        )

        # Deterministic demonstration enrollments.
        session.add_all(
            [
                ContestParticipant(
                    contest_id=contest_a.id,
                    actor_id="student-a",
                ),

                ContestParticipant(
                    contest_id=contest_b.id,
                    actor_id="student-b",
                ),
            ]
        )


# ============================================================
# DOCKER HEALTH
# ============================================================

def docker_available() -> bool:

    client = None

    try:

        client = docker.from_env()

        client.ping()

        return True

    except DockerException:

        logger.exception(
            "Docker health check failed"
        )

        return False

    finally:

        if client:

            try:
                client.close()

            except Exception:
                pass


# ============================================================
# APPLICATION LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(
    _: FastAPI,
) -> AsyncIterator[None]:

    global _worker_thread

    try:

        with engine.connect() as connection:

            connection.execute(
                text("SELECT 1")
            )

        Base.metadata.create_all(
            bind=engine,
        )

        ensure_schema_compatibility()

        seed()

        # Recover old jobs at application startup.
        recover_interrupted_submissions()

        logger.info(
            "Database initialization completed"
        )

    except SQLAlchemyError as exc:

        database_location = engine.url.render_as_string(
            hide_password=True,
        )

        logger.exception(
            "Database initialization failed for %s",
            database_location,
        )

        raise RuntimeError(
            f"Database unavailable at {database_location}. "
            "Start PostgreSQL and verify DATABASE_URL."
        ) from exc

    if os.getenv(
        "RUN_JUDGE_WORKER",
        "1",
    ) == "1":

        _worker_stop.clear()

        _worker_thread = threading.Thread(
            target=worker_loop,
            daemon=True,
            name="shodh-judge-worker",
        )

        _worker_thread.start()

        logger.info(
            "Judge worker started"
        )

    try:

        yield

    finally:

        _worker_stop.set()

        if _worker_thread:

            _worker_thread.join(
                timeout=5,
            )

            _worker_thread = None

        engine.dispose()


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Shodh Contest API",
    version="1.0",
    lifespan=lifespan,
)


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health() -> dict[str, Any]:

    database_available = False

    try:

        with engine.connect() as connection:

            connection.execute(
                text("SELECT 1")
            )

        database_available = True

    except SQLAlchemyError:

        logger.exception(
            "Database health check failed"
        )

    judge_available = docker_available()

    return {
        "status": (
            "ok"
            if database_available
            else "error"
        ),

        "database_available": database_available,

        "execution_mode": (
            "docker"
            if judge_available
            else "unavailable"
        ),

        "judge_available": judge_available,
    }


# ============================================================
# AUTH
# ============================================================

@app.get("/auth/me")
def get_identity(
    user=Depends(identity),
) -> dict[str, str]:

    return {
        "user_id": user[0],
        "organization_id": user[1],
        "role": user[2],
    }


# ============================================================
# CONTESTS
# ============================================================

@app.get("/contests")
def list_contests(
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> list[dict[str, Any]]:

    rows = session.scalars(
        select(Contest)
        .where(
            Contest.organization_id == user[1]
        )
        .order_by(
            Contest.id
        )
    ).all()

    return [
        {
            "id": contest.id,
            "title": contest.title,
            "opens_at": contest.opens_at,
            "closes_at": contest.closes_at,
        }
        for contest in rows
    ]


@app.post("/contests")
def create_contest(
    body: ContestCreate,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    organizer(user)

    now = utcnow()

    opens_at = now + timedelta(
        minutes=body.opens_in_minutes,
    )

    closes_at = opens_at + timedelta(
        minutes=body.duration_minutes,
    )

    contest = Contest(
        id=str(uuid.uuid4()),
        organization_id=user[1],
        title=body.title,
        opens_at=opens_at,
        closes_at=closes_at,
    )

    session.add(contest)

    session.commit()

    session.refresh(contest)

    return {
        "id": contest.id,
        "title": contest.title,
        "opens_at": contest.opens_at,
        "closes_at": contest.closes_at,
    }


@app.get("/contests/{contest_id}")
def get_contest(
    contest_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    contest = contest_for(
        session,
        contest_id,
        user,
    )

    return {
        "id": contest.id,
        "title": contest.title,
        "organization_id": contest.organization_id,
        "opens_at": contest.opens_at,
        "closes_at": contest.closes_at,
    }


@app.post(
    "/contests/{contest_id}/join",
    status_code=204,
)
def join(
    contest_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> None:

    participant(user)

    contest = contest_for(
        session,
        contest_id,
        user,
    )

    enrollment = session.get(
        ContestParticipant,
        (
            contest.id,
            user[0],
        ),
    )

    if enrollment:
        return

    session.add(
        ContestParticipant(
            contest_id=contest.id,
            actor_id=user[0],
        )
    )

    try:

        session.commit()

    except IntegrityError:

        session.rollback()

        if not session.get(
            ContestParticipant,
            (
                contest.id,
                user[0],
            ),
        ):

            raise HTTPException(
                status_code=503,
                detail="Could not join contest",
            )


# ============================================================
# PROBLEMS
# ============================================================

@app.post("/contests/{contest_id}/problems")
def create_problem(
    contest_id: str,
    body: ProblemCreate,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    organizer(user)

    contest = contest_for(
        session,
        contest_id,
        user,
    )

    tests = _normalize_test_cases(
        body.tests,
    )

    problem = Problem(
        id=str(uuid.uuid4()),
        contest_id=contest.id,
        title=body.title,
        version=1,
        tests=tests,
        active=body.active,
    )

    session.add(problem)

    session.commit()

    session.refresh(problem)

    return {
        "id": problem.id,
        "title": problem.title,
        "version": problem.version,
        "active": problem.active,
    }


@app.put("/problems/{problem_id}")
def update_problem(
    problem_id: str,
    body: ProblemUpdate,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    organizer(user)

    problem = session.get(
        Problem,
        problem_id,
    )

    if not problem:

        raise HTTPException(
            status_code=404,
            detail="Problem not found",
        )

    contest_for(
        session,
        problem.contest_id,
        user,
    )

    if body.title is not None:

        problem.title = body.title

    if body.tests is not None:

        tests = _normalize_test_cases(
            body.tests,
        )

        if tests != problem.tests:

            problem.tests = tests

            problem.version += 1

    if body.active is not None:

        problem.active = body.active

    session.commit()

    return {
        "id": problem.id,
        "title": problem.title,
        "version": problem.version,
        "active": problem.active,
    }


@app.get("/contests/{contest_id}/problems")
def list_problems(
    contest_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> list[dict[str, Any]]:

    contest_for(
        session,
        contest_id,
        user,
    )

    rows = session.scalars(
        select(Problem)
        .where(
            Problem.contest_id == contest_id
        )
        .order_by(
            Problem.id
        )
    ).all()

    result = []

    for problem in rows:

        public_tests = [
            {
                "input": case.get("input", ""),
                "expected": case.get("expected", ""),
                "hidden": False,
            }
            for case in problem.tests
            if not case.get("hidden", False)
        ]

        result.append(
            {
                "id": problem.id,
                "title": problem.title,
                "version": problem.version,
                "active": problem.active,
                "tests": public_tests,
            }
        )

    return result


@app.get("/problems/{problem_id}")
def get_problem(
    problem_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    problem = session.get(
        Problem,
        problem_id,
    )

    if not problem:

        raise HTTPException(
            status_code=404,
            detail="Problem not found",
        )

    contest_for(
        session,
        problem.contest_id,
        user,
    )

    public_tests = [
        {
            "input": case.get("input", ""),
            "expected": case.get("expected", ""),
            "hidden": False,
        }
        for case in problem.tests
        if not case.get("hidden", False)
    ]

    return {
        "id": problem.id,
        "title": problem.title,
        "version": problem.version,
        "active": problem.active,
        "tests": public_tests,
    }


# ============================================================
# SUBMISSIONS
# ============================================================

@app.post(
    "/contests/{contest_id}/submissions",
    status_code=202,
)
def submit(
    contest_id: str,
    body: SubmissionRequest,

    idempotency_key: str = Header(
        min_length=1,
        max_length=128,
        alias="Idempotency-Key",
    ),

    user=Depends(identity),

    session: Session = Depends(db_session),
) -> dict[str, Any]:

    participant(user)

    contest = contest_for(
        session,
        contest_id,
        user,
    )

    now = utcnow()

    if (
        now < as_utc(contest.opens_at)
        or now >= as_utc(contest.closes_at)
    ):

        raise HTTPException(
            status_code=409,
            detail="Contest is not open",
        )

    enrollment = session.get(
        ContestParticipant,
        (
            contest.id,
            user[0],
        ),
    )

    if not enrollment:

        raise HTTPException(
            status_code=403,
            detail="Join the contest before submitting",
        )

    if body.language != "python":

        raise HTTPException(
            status_code=422,
            detail="Only Python 3 is supported",
        )

    problem = session.get(
        Problem,
        body.problem_id,
    )

    if (
        not problem
        or problem.contest_id != contest.id
        or not problem.active
    ):

        raise HTTPException(
            status_code=404,
            detail="Problem not found",
        )

    digest = hashlib.sha256(
        json.dumps(
            {
                "problem_id": body.problem_id,
                "language": body.language,
                "source": body.source,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()

    existing = session.scalar(
        select(Submission).where(
            Submission.contest_id == contest_id,
            Submission.actor_id == user[0],
            Submission.idempotency_key == idempotency_key,
        )
    )

    if existing:

        if existing.request_hash != digest:

            raise HTTPException(
                status_code=409,
                detail="Idempotency key reused with different request",
            )

        return {
            "submission_id": existing.id,
            "status": existing.status,
        }

    submission = Submission(
        id=str(uuid.uuid4()),
        contest_id=contest.id,
        problem_id=problem.id,
        actor_id=user[0],
        idempotency_key=idempotency_key,
        request_hash=digest,
        source=body.source,
        problem_version=problem.version,
        test_snapshot=problem.tests,
        status="queued",
        score=0,
    )

    session.add(submission)

    try:

        session.commit()

    except IntegrityError:

        session.rollback()

        existing = session.scalar(
            select(Submission).where(
                Submission.contest_id == contest_id,
                Submission.actor_id == user[0],
                Submission.idempotency_key == idempotency_key,
            )
        )

        if not existing:

            raise HTTPException(
                status_code=503,
                detail="Could not persist submission",
            )

        if existing.request_hash != digest:

            raise HTTPException(
                status_code=409,
                detail="Idempotency key reused with different request",
            )

        return {
            "submission_id": existing.id,
            "status": existing.status,
        }

    return {
        "submission_id": submission.id,
        "status": submission.status,
    }


# ============================================================
# SUBMISSION LIST
# ============================================================

@app.get("/contests/{contest_id}/submissions")
def list_contest_submissions(
    contest_id: str,

    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),

    offset: int = Query(
        default=0,
        ge=0,
    ),

    status: str | None = Query(
        default=None,
        pattern="^(queued|running|completed|failed)$",
    ),

    problem_id: str | None = Query(
        default=None,
        min_length=1,
    ),

    user=Depends(identity),

    session: Session = Depends(db_session),
) -> dict[str, Any]:

    contest_for(
        session,
        contest_id,
        user,
    )

    statement = select(Submission).where(
        Submission.contest_id == contest_id
    )

    if user[2] == "participant":

        statement = statement.where(
            Submission.actor_id == user[0]
        )

    if status is not None:

        statement = statement.where(
            Submission.status == status
        )

    if problem_id is not None:

        statement = statement.where(
            Submission.problem_id == problem_id
        )

    total = session.scalar(
        select(func.count())
        .select_from(
            statement.subquery()
        )
    ) or 0

    rows = session.scalars(
        statement.order_by(
            Submission.created_at.desc(),
            Submission.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "items": [
            {
                "id": submission.id,
                "participant_id": submission.actor_id,
                "problem_id": submission.problem_id,
                "status": submission.status,
                "verdict": submission.verdict,
                "score": submission.score,
                "attempts": submission.attempts_count,
                "created_at": submission.created_at,
            }
            for submission in rows
        ],

        "total": total,

        "limit": limit,

        "offset": offset,
    }


# ============================================================
# GET SUBMISSION
# ============================================================

@app.get("/submissions/{submission_id}")
def get_submission(
    submission_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    sub = session.get(
        Submission,
        submission_id,
    )

    if not sub:

        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    contest_for(
        session,
        sub.contest_id,
        user,
    )

    if (
        user[2] == "participant"
        and sub.actor_id != user[0]
    ):

        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    return {
        "id": sub.id,
        "status": sub.status,
        "verdict": sub.verdict,
        "score": sub.score,
        "attempts": sub.attempts_count,
        "problem_id": sub.problem_id,
    }


# ============================================================
# JUDGE ATTEMPTS
# ============================================================

@app.get("/submissions/{submission_id}/attempts")
def get_judge_attempts(
    submission_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> list[dict[str, Any]]:

    sub = session.get(
        Submission,
        submission_id,
    )

    if not sub:

        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    contest_for(
        session,
        sub.contest_id,
        user,
    )

    if (
        user[2] == "participant"
        and sub.actor_id != user[0]
    ):

        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    attempts = session.scalars(
        select(JudgeAttempt)
        .where(
            JudgeAttempt.submission_id == sub.id
        )
        .order_by(
            JudgeAttempt.number
        )
    ).all()

    return [
        {
            "id": attempt.id,
            "number": attempt.number,
            "problem_version": attempt.problem_version,
            "verdict": attempt.verdict,
            "mode": attempt.mode,
            "detail": attempt.detail,
            "started_at": attempt.started_at,
            "finished_at": attempt.finished_at,
        }
        for attempt in attempts
    ]


# ============================================================
# LEADERBOARD
# ============================================================

@app.get("/contests/{contest_id}/leaderboard")
def leaderboard(
    contest_id: str,

    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),

    offset: int = Query(
        default=0,
        ge=0,
    ),

    user=Depends(identity),

    session: Session = Depends(db_session),
) -> list[dict[str, Any]]:

    contest_for(
        session,
        contest_id,
        user,
    )

    rows = session.scalars(
        select(Submission)
        .where(
            Submission.contest_id == contest_id,
            Submission.status == "completed",
        )
        .order_by(
            Submission.created_at,
            Submission.id,
        )
    ).all()

    scores: dict[str, dict[str, Any]] = {
        actor_id: {
            "participant_id": actor_id,
            "score": 0,
            "penalty_minutes": 0,
            "solved": 0,
        }
        for actor_id in session.scalars(
            select(ContestParticipant.actor_id)
            .where(
                ContestParticipant.contest_id == contest_id
            )
        ).all()
    }

    first_accept: dict[
        tuple[str, str],
        Submission,
    ] = {}

    wrong_before: dict[
        tuple[str, str],
        int,
    ] = {}

    for sub in rows:

        key = (
            sub.actor_id,
            sub.problem_id,
        )

        if (
            sub.verdict == "accepted"
            and key not in first_accept
        ):

            first_accept[key] = sub

        elif (
            sub.verdict
            in {
                "wrong_answer",
                "runtime_error",
                "time_limit",
            }
            and key not in first_accept
        ):

            wrong_before[key] = (
                wrong_before.get(key, 0) + 1
            )

    contest = session.get(
        Contest,
        contest_id,
    )

    contest_open = (
        as_utc(contest.opens_at)
        if contest
        else utcnow()
    )

    for (
        actor,
        problem_id,
    ), accepted in first_accept.items():

        row = scores.setdefault(
            actor,
            {
                "participant_id": actor,
                "score": 0,
                "penalty_minutes": 0,
                "solved": 0,
            },
        )

        row["score"] += 100

        row["solved"] += 1

        row["penalty_minutes"] += max(
            0,
            int(
                (
                    as_utc(accepted.created_at)
                    - contest_open
                ).total_seconds()
                // 60
            ),
        ) + 20 * wrong_before.get(
            (
                actor,
                problem_id,
            ),
            0,
        )

    ranked = sorted(
        scores.values(),
        key=lambda item: (
            -item["score"],
            item["penalty_minutes"],
            item["participant_id"],
        ),
    )

    return ranked[
        offset:offset + limit
    ]


# ============================================================
# REJUDGE
# ============================================================

@app.post(
    "/submissions/{submission_id}/rejudge",
    status_code=202,
)
def rejudge(
    submission_id: str,
    user=Depends(identity),
    session: Session = Depends(db_session),
) -> dict[str, Any]:

    organizer(user)

    sub = session.get(
        Submission,
        submission_id,
    )

    if not sub:

        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        )

    contest_for(
        session,
        sub.contest_id,
        user,
    )

    # Do not supersede a judge process which is actively running.
    if sub.status == "running":

        raise HTTPException(
            status_code=409,
            detail="Cannot rejudge a submission while it is running",
        )

    problem = session.get(
        Problem,
        sub.problem_id,
    )

    if problem:

        sub.problem_version = problem.version

        sub.test_snapshot = problem.tests

    sub.status = "queued"

    sub.verdict = None

    sub.score = 0

    sub.active_attempt_id = None

    # Reset the retry counter for the new judging cycle.
    # Historical JudgeAttempt rows are NOT deleted.
    sub.attempts_count = 0

    session.commit()

    return {
        "submission_id": sub.id,
        "status": "queued",
        "problem_version": sub.problem_version,
    }


# ============================================================
# DOCKER JUDGE - OUTPUT LIMIT ENFORCEMENT
# ============================================================

def _judge(
    source: str,
    tests: list[Any],
) -> tuple[str, str | None]:

    client = None

    # --------------------------------------------------------
    # CONNECT TO DOCKER
    # --------------------------------------------------------

    try:

        client = docker.from_env()
        client.ping()

    except DockerException as exc:

        if client:
            try:
                client.close()
            except Exception:
                pass

        logger.exception(
            "Docker connection failed"
        )

        return (
            "judge_error",
            f"Docker unavailable: {type(exc).__name__}: {str(exc)[:150]}",
        )

    # --------------------------------------------------------
    # EXECUTE TEST CASES
    # --------------------------------------------------------

    try:

        with tempfile.TemporaryDirectory(
            prefix="shodh-judge-",
        ) as temp_dir:

            work = Path(temp_dir)

            source_file = work / "main.py"
            input_file = work / "input.txt"
            runner_file = work / "runner.py"

            source_file.write_text(
                source,
                encoding="utf-8",
            )

            # Trusted runner runs inside the container. It captures
            # at most MAX_OUTPUT_BYTES + 1 bytes from the student's
            # stdout. The extra byte proves that the limit was crossed.
            # It kills the student's process group immediately when
            # that extra byte is received, without relying on Docker logs.
            runner_code = r'''
import os
import signal
import subprocess
import sys
import threading

OUTPUT_LIMIT = int(sys.argv[2])
STDERR_LIMIT = 4096

stderr_data = bytearray()

process = subprocess.Popen(
    [
        sys.executable,
        "-I",
        sys.argv[1],
    ],
    stdin=None,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0,
    start_new_session=True,
)


def drain_stderr():
    while True:
        chunk = process.stderr.read(8192)

        if not chunk:
            break

        remaining = STDERR_LIMIT - len(stderr_data)

        if remaining > 0:
            stderr_data.extend(chunk[:remaining])


stderr_thread = threading.Thread(
    target=drain_stderr,
    daemon=True,
)

stderr_thread.start()

captured_stdout = bytearray()

while True:
    remaining = OUTPUT_LIMIT + 1 - len(captured_stdout)

    if remaining <= 0:
        break

    chunk = process.stdout.read(
        min(8192, remaining)
    )

    if not chunk:
        break

    captured_stdout.extend(chunk)

    if len(captured_stdout) > OUTPUT_LIMIT:
        break


if len(captured_stdout) > OUTPUT_LIMIT:
    try:
        os.killpg(
            process.pid,
            signal.SIGKILL,
        )

    except ProcessLookupError:
        pass

    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    stderr_thread.join(timeout=2)

    sys.stderr.buffer.write(
        b"SHODH_OUTPUT_LIMIT_EXCEEDED\n"
    )
    sys.stderr.buffer.flush()

    sys.exit(86)


exit_code = process.wait()
stderr_thread.join(timeout=2)

sys.stdout.buffer.write(
    captured_stdout
)
sys.stdout.buffer.flush()

sys.stderr.buffer.write(
    stderr_data
)
sys.stderr.buffer.flush()

sys.exit(
    exit_code if exit_code >= 0 else 1
)
'''

            runner_file.write_text(
                runner_code,
                encoding="utf-8",
            )

            for case in tests:

                container = None

                try:

                    input_data = str(
                        case.get("input", "")
                    )

                    input_file.write_text(
                        input_data,
                        encoding="utf-8",
                    )

                    container = client.containers.create(

                        DOCKER_IMAGE,

                        command=[
                            "sh",
                            "-c",
                            (
                                "python -I /work/runner.py "
                                f"/work/main.py {MAX_OUTPUT_BYTES} "
                                "< /work/input.txt"
                            ),
                        ],

                        stdin_open=False,
                        tty=False,

                        network_disabled=True,

                        user="65534:65534",

                        read_only=True,

                        cap_drop=["ALL"],

                        security_opt=[
                            "no-new-privileges:true",
                        ],

                        mem_limit="128m",

                        nano_cpus=500_000_000,

                        pids_limit=32,

                        tmpfs={
                            "/tmp": (
                                "rw,noexec,nosuid,size=16m"
                            ),
                        },

                        volumes={
                            str(work): {
                                "bind": "/work",
                                "mode": "ro",
                            },
                        },

                        working_dir="/tmp",

                        detach=True,

                        labels={
                            "shodh.judge": "true",
                        },

                        # The runner emits no more than the output
                        # limit plus one byte, so normal Docker logs
                        # stay bounded and log rotation is not used
                        # to determine the verdict.
                        log_config=docker.types.LogConfig(
                            type="json-file",
                            config={
                                "max-size": "256k",
                                "max-file": "1",
                            },
                        ),
                    )

                    container.start()

                    # ------------------------------------------------
                    # ENFORCE WALL TIME LIMIT
                    # ------------------------------------------------

                    wait_result: dict[str, Any] = {}
                    wait_finished = threading.Event()

                    def wait_for_container() -> None:
                        try:
                            wait_result["result"] = container.wait()
                        except Exception as wait_exc:
                            wait_result["exception"] = wait_exc
                        finally:
                            wait_finished.set()

                    wait_thread = threading.Thread(
                        target=wait_for_container,
                        daemon=True,
                        name="shodh-container-wait",
                    )

                    wait_thread.start()

                    completed = wait_finished.wait(
                        timeout=WALL_SECONDS,
                    )

                    if not completed:

                        logger.warning(
                            "Judge time limit exceeded: %s seconds",
                            WALL_SECONDS,
                        )

                        try:
                            container.kill()
                        except Exception:
                            logger.warning(
                                "Container kill failed after timeout",
                                exc_info=True,
                            )

                        return (
                            "time_limit",
                            None,
                        )

                    # ------------------------------------------------
                    # HANDLE DOCKER WAIT ERRORS
                    # ------------------------------------------------

                    if "exception" in wait_result:

                        wait_exc = wait_result["exception"]
                        exception_name = type(wait_exc).__name__
                        exception_message = str(wait_exc).lower()

                        logger.error(
                            "Docker wait failed: %s",
                            wait_exc,
                        )

                        if (
                            "timeout" in exception_name.lower()
                            or "timeout" in exception_message
                            or "timed out" in exception_message
                        ):

                            try:
                                container.kill()
                            except Exception:
                                pass

                            return (
                                "time_limit",
                                None,
                            )

                        return (
                            "judge_error",
                            (
                                f"Docker wait error: "
                                f"{exception_name}: "
                                f"{str(wait_exc)[:150]}"
                            ),
                        )

                    result = wait_result.get(
                        "result",
                        {},
                    )

                    exit_code = result.get(
                        "StatusCode",
                        1,
                    )

                    # Exit code 86 is reserved by runner.py for OLE.
                    # This check does not depend on the Docker log size.
                    if exit_code == 86:

                        logger.warning(
                            "Output limit exceeded",
                        )

                        return (
                            "output_limit",
                            None,
                        )

                    # ------------------------------------------------
                    # COLLECT STDOUT AND STDERR
                    # ------------------------------------------------

                    stdout = container.logs(
                        stdout=True,
                        stderr=False,
                    )

                    stderr = container.logs(
                        stdout=False,
                        stderr=True,
                    )

                    # Defensive fallback in case a Docker logging
                    # configuration behaves unexpectedly.
                    if len(stdout) > MAX_OUTPUT_BYTES:

                        logger.warning(
                            "Output limit exceeded in captured Docker logs",
                        )

                        return (
                            "output_limit",
                            None,
                        )

                    output = stdout.decode(
                        "utf-8",
                        errors="replace",
                    )

                    error_output = stderr[
                        :MAX_OUTPUT_BYTES
                    ].decode(
                        "utf-8",
                        errors="replace",
                    )

                    # ------------------------------------------------
                    # CHECK EXIT CODE
                    # ------------------------------------------------

                    if exit_code != 0:

                        logger.warning(
                            "Program exited with code %s. stderr: %s",
                            exit_code,
                            error_output[:500],
                        )

                        if (
                            "SyntaxError" in error_output
                            or "IndentationError" in error_output
                        ):

                            return (
                                "compilation_error",
                                None,
                            )

                        return (
                            "runtime_error",
                            None,
                        )

                    # ------------------------------------------------
                    # COMPARE OUTPUT
                    # ------------------------------------------------

                    expected = str(
                        case.get("expected", "")
                    ).strip()

                    actual = output.strip()

                    if actual != expected:

                        logger.info(
                            "Wrong answer. Expected=%r Actual=%r",
                            expected,
                            actual,
                        )

                        return (
                            "wrong_answer",
                            None,
                        )

                finally:

                    # ------------------------------------------------
                    # REMOVE CONTAINER
                    # ------------------------------------------------

                    if container:

                        try:
                            container.remove(
                                force=True,
                            )

                        except (
                            DockerException,
                            NotFound,
                        ):

                            logger.warning(
                                "Container cleanup failed",
                                exc_info=True,
                            )

            return (
                "accepted",
                None,
            )

    except DockerException as exc:

        logger.exception(
            "Docker judge error",
        )

        return (
            "judge_error",
            (
                f"Docker error: "
                f"{type(exc).__name__}: "
                f"{str(exc)[:180]}"
            ),
        )

    except Exception as exc:

        logger.exception(
            "Unexpected judge error",
        )

        return (
            "judge_error",
            (
                f"{type(exc).__name__}: "
                f"{str(exc)[:180]}"
            ),
        )

    finally:

        if client:

            try:
                client.close()
            except Exception:
                pass


# ============================================================
# WORKER: RECOVER INTERRUPTED SUBMISSIONS
# ============================================================

def recover_interrupted_submissions() -> int:
    """
    Recover submissions abandoned by crashed workers.

    A worker periodically updates its attempt heartbeat.
    Only submissions with stale heartbeats are recovered.

    PostgreSQL row locking prevents multiple workers from
    recovering the same submission simultaneously.
    """

    cutoff = utcnow() - timedelta(
        seconds=RECOVERY_STALE_SECONDS,
    )

    recovered = 0

    with SessionLocal.begin() as session:

        submissions = session.scalars(
            select(Submission)
            .where(
                Submission.status == "running",
            )
            .order_by(
                Submission.created_at,
            )
            .with_for_update(
                skip_locked=True,
            )
        ).all()

        for sub in submissions:

            attempt = None

            if sub.active_attempt_id:

                # Lock the attempt row while checking its lease.
                # A live heartbeat update will cause SKIP LOCKED,
                # so this recovery pass will leave that job alone.
                attempt = session.scalar(
                    select(JudgeAttempt)
                    .where(
                        JudgeAttempt.id == sub.active_attempt_id,
                    )
                    .with_for_update(
                        skip_locked=True,
                    )
                )

                if attempt is None:

                    # The attempt may be locked by a live heartbeat.
                    # Do not mistake a locked row for a missing row.
                    continue

                last_activity = (
                    attempt.heartbeat_at
                    or attempt.started_at
                )

            else:

                last_activity = sub.created_at

            if last_activity is not None:

                if as_utc(last_activity) > cutoff:
                    continue

            # Record the abandoned attempt.
            if (
                attempt
                and attempt.finished_at is None
            ):

                attempt.verdict = "judge_error"

                attempt.detail = (
                    "Judging interrupted after worker heartbeat expired"
                )

                attempt.finished_at = utcnow()

            sub.active_attempt_id = None

            # Retry if attempts remain.
            if sub.attempts_count < MAX_ATTEMPTS:

                sub.status = "queued"

                sub.verdict = None

                sub.score = 0

            else:

                sub.status = "failed"

                sub.verdict = "judge_error"

                sub.score = 0

            recovered += 1

    if recovered:

        logger.warning(
            "Recovered %s interrupted submissions",
            recovered,
        )

    return recovered


# ============================================================
# WORKER: HEARTBEAT
# ============================================================

def _heartbeat_loop(
    attempt_id: str,
    stop_event: threading.Event,
) -> None:
    """
    Refresh the active judge attempt heartbeat.

    Recovery checks this timestamp before requeueing a job.
    """

    while not stop_event.wait(
        HEARTBEAT_INTERVAL_SECONDS,
    ):

        try:

            with SessionLocal.begin() as session:

                result = session.execute(
                    update(JudgeAttempt)
                    .where(
                        JudgeAttempt.id == attempt_id,
                        JudgeAttempt.finished_at.is_(None),
                    )
                    .values(
                        heartbeat_at=utcnow(),
                    )
                )

                if result.rowcount == 0:

                    # The attempt has already finished or
                    # has been recovered by another worker.
                    return

        except SQLAlchemyError:

            logger.exception(
                "Judge heartbeat update failed for attempt %s",
                attempt_id,
            )


# ============================================================
# WORKER: CLAIM SUBMISSION
# ============================================================

def _claim() -> tuple[
    str,
    str,
    str,
    list[Any],
] | None:

    with SessionLocal.begin() as session:

        sub = session.scalar(
            select(Submission)
            .where(
                Submission.status == "queued"
            )
            .order_by(
                Submission.created_at,
                Submission.id,
            )
            .with_for_update(
                skip_locked=True
            )
            .limit(1)
        )

        if not sub:
            return None

        if sub.attempts_count >= MAX_ATTEMPTS:

            sub.status = "failed"

            sub.verdict = "judge_error"

            sub.score = 0

            sub.active_attempt_id = None

            return None

        sub.attempts_count += 1

        attempt_id = str(
            uuid.uuid4()
        )

        # Keep attempt numbers sequential across rejudges.
        # attempts_count tracks retries in the current cycle.
        previous_number = session.scalar(
            select(
                func.max(JudgeAttempt.number)
            ).where(
                JudgeAttempt.submission_id == sub.id
            )
        ) or 0

        attempt_number = previous_number + 1

        started_at = utcnow()

        sub.active_attempt_id = attempt_id

        sub.status = "running"

        attempt = JudgeAttempt(
            id=attempt_id,
            submission_id=sub.id,
            number=attempt_number,
            problem_version=sub.problem_version,
            verdict="running",
            mode="docker",
            started_at=started_at,
            heartbeat_at=started_at,
        )

        session.add(attempt)

        return (
            sub.id,
            attempt_id,
            sub.source,
            sub.test_snapshot,
        )


# ============================================================
# WORKER: FINISH SUBMISSION
# ============================================================

def _finish(
    submission_id: str,
    attempt_id: str,
    verdict: str,
    detail: str | None,
) -> None:

    with SessionLocal.begin() as session:

        sub = session.get(
            Submission,
            submission_id,
        )

        attempt = session.get(
            JudgeAttempt,
            attempt_id,
        )

        if not sub or not attempt:
            return

        # Do not overwrite an attempt already marked interrupted.
        if attempt.finished_at is not None:
            return

        attempt.verdict = verdict

        attempt.detail = detail

        attempt.finished_at = utcnow()

        if sub.active_attempt_id != attempt_id:

            logger.warning(
                "Ignoring stale judge result for submission %s",
                submission_id,
            )

            return

        if (
            verdict == "judge_error"
            and sub.attempts_count < MAX_ATTEMPTS
        ):

            sub.status = "queued"

            sub.verdict = None

            sub.score = 0

            return

        sub.status = (
            "failed"
            if verdict == "judge_error"
            else "completed"
        )

        sub.verdict = verdict

        sub.score = (
            100
            if verdict == "accepted"
            else 0
        )


# ============================================================
# WORKER LOOP
# ============================================================

def worker_loop() -> None:

    logger.info(
        "Judge worker loop started"
    )

    next_recovery = (
        time.monotonic()
        + RECOVERY_INTERVAL_SECONDS
    )

    while not _worker_stop.is_set():

        try:

            # Periodically recover abandoned judge attempts.
            current_time = time.monotonic()

            if current_time >= next_recovery:

                recover_interrupted_submissions()

                next_recovery = (
                    current_time
                    + RECOVERY_INTERVAL_SECONDS
                )

            task = _claim()

            if task:

                (
                    sub_id,
                    attempt_id,
                    source,
                    tests,
                ) = task

                heartbeat_stop = threading.Event()

                heartbeat_thread = threading.Thread(
                    target=_heartbeat_loop,
                    args=(
                        attempt_id,
                        heartbeat_stop,
                    ),
                    daemon=True,
                    name=f"shodh-heartbeat-{attempt_id[:8]}",
                )

                heartbeat_thread.start()

                try:

                    verdict, detail = _judge(
                        source,
                        tests,
                    )

                except Exception as exc:

                    logger.exception(
                        "Unexpected judge execution error "
                        "for submission %s",
                        sub_id,
                    )

                    verdict = "judge_error"

                    detail = (
                        f"{type(exc).__name__}: "
                        f"{str(exc)[:180]}"
                    )

                finally:

                    heartbeat_stop.set()

                    heartbeat_thread.join(
                        timeout=2,
                    )

                _finish(
                    sub_id,
                    attempt_id,
                    verdict,
                    detail,
                )

            else:

                _worker_stop.wait(
                    0.5,
                )

        except Exception:

            logger.exception(
                "Judge worker cycle failed"
            )

            _worker_stop.wait(
                1,
            )

    logger.info(
        "Judge worker loop stopped"
    )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "soudh:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )