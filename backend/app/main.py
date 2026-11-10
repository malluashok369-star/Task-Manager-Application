from contextlib import contextmanager
from datetime import date, datetime, timezone
import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "tasks.db"
PASSWORD_ITERATIONS = 600_000
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address")
        return value


class User(BaseModel):
    id: int
    name: str
    email: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] = "medium"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] | None = None
    completed: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class Task(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    due_date: date | None
    priority: Literal["low", "medium", "high"]
    completed: bool
    created_at: datetime
    updated_at: datetime


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, encoded_salt, encoded_digest = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt)
        expected_digest = base64.urlsafe_b64decode(encoded_digest)
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual_digest, expected_digest)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    database_path = Path(db_path or os.getenv("TASKS_DB_PATH", DEFAULT_DB_PATH))
    database_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect() -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
                description TEXT NOT NULL DEFAULT '',
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'medium'
                    CHECK(priority IN ('low', 'medium', 'high')),
                completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        task_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        if "user_id" not in task_columns:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
            )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_tasks_user_id ON tasks(user_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        configured_secret = os.getenv("SESSION_SECRET")
        if configured_secret:
            session_secret = configured_secret
        else:
            setting = connection.execute(
                "SELECT value FROM app_settings WHERE key = 'session_secret'"
            ).fetchone()
            session_secret = setting["value"] if setting else secrets.token_urlsafe(48)
            if setting is None:
                connection.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('session_secret', ?)",
                    (session_secret,),
                )

    app = FastAPI(
        title="Daymark Task Manager",
        description="A lightweight REST API for managing personal tasks.",
        version="1.0.0",
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="daymark_session",
        max_age=60 * 60 * 24 * 7,
        same_site="lax",
        https_only=os.getenv("COOKIE_SECURE", "").casefold() == "true",
    )
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
    dummy_password_hash = _hash_password(secrets.token_urlsafe(32))

    def authenticated_user(request: Request) -> sqlite3.Row:
        user_id = request.session.get("user_id")
        if not isinstance(user_id, int):
            raise HTTPException(status_code=401, detail="Please sign in to continue")
        with connect() as connection:
            user = connection.execute(
                "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if user is None:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Please sign in to continue")
        return user

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/register", response_model=User, status_code=201)
    def register(payload: RegisterRequest, request: Request) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM users WHERE email = ?", (payload.email,)
            ).fetchone():
                raise HTTPException(status_code=409, detail="An account with this email already exists")
            first_user = connection.execute(
                "SELECT 1 FROM users LIMIT 1"
            ).fetchone() is None
            cursor = connection.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (payload.name, payload.email, _hash_password(payload.password), now),
            )
            user_id = cursor.lastrowid
            if first_user:
                connection.execute(
                    "UPDATE tasks SET user_id = ? WHERE user_id IS NULL", (user_id,)
                )
            user = connection.execute(
                "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        request.session.clear()
        request.session["user_id"] = user["id"]
        return dict(user)

    @app.post("/api/auth/login", response_model=User)
    def login(payload: LoginRequest, request: Request) -> dict:
        with connect() as connection:
            user = connection.execute(
                "SELECT id, name, email, password_hash FROM users WHERE email = ?",
                (payload.email,),
            ).fetchone()
        stored_hash = user["password_hash"] if user is not None else dummy_password_hash
        if not _verify_password(payload.password, stored_hash) or user is None:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        request.session.clear()
        request.session["user_id"] = user["id"]
        return {"id": user["id"], "name": user["name"], "email": user["email"]}

    @app.get("/api/auth/me")
    def current_user(request: Request) -> dict:
        user_id = request.session.get("user_id")
        if not isinstance(user_id, int):
            return {"authenticated": False, "user": None}
        with connect() as connection:
            user = connection.execute(
                "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if user is None:
            request.session.clear()
            return {"authenticated": False, "user": None}
        return {"authenticated": True, "user": dict(user)}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        request.session.clear()
        return Response(status_code=204)

    @app.get("/api/tasks", response_model=list[Task])
    def list_tasks(
        request: Request,
        status: Literal["all", "completed", "pending"] = Query(default="all"),
    ) -> list[dict]:
        user = authenticated_user(request)
        conditions = "WHERE user_id = ?"
        parameters: tuple[int, ...] = (user["id"],)
        if status == "completed":
            conditions += " AND completed = ?"
            parameters = (*parameters, 1)
        elif status == "pending":
            conditions += " AND completed = ?"
            parameters = (*parameters, 0)
        with connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks {conditions} ORDER BY completed ASC, "
                "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "due_date IS NULL, due_date ASC, created_at DESC",
                parameters,
            ).fetchall()
        return [_serialize_task(row) for row in rows]

    @app.post("/api/tasks", response_model=Task, status_code=201)
    def create_task(payload: TaskCreate, request: Request) -> dict:
        user = authenticated_user(request)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks
                    (user_id, title, description, due_date, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    payload.title,
                    payload.description,
                    payload.due_date.isoformat() if payload.due_date else None,
                    payload.priority,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (cursor.lastrowid, user["id"]),
            ).fetchone()
        return _serialize_task(row)

    @app.get("/api/tasks/{task_id}", response_model=Task)
    def get_task(task_id: int, request: Request) -> dict:
        user = authenticated_user(request)
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return _serialize_task(row)

    @app.patch("/api/tasks/{task_id}", response_model=Task)
    def update_task(task_id: int, payload: TaskUpdate, request: Request) -> dict:
        user = authenticated_user(request)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=400, detail="Provide at least one field to update")
        if any(value is None and field != "due_date" for field, value in changes.items()):
            raise HTTPException(status_code=422, detail="Updated fields cannot be null")

        if changes.get("due_date") is not None:
            changes["due_date"] = changes["due_date"].isoformat()
        if "completed" in changes:
            changes["completed"] = int(changes["completed"])
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = (*changes.values(), task_id, user["id"])

        with connect() as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ? AND user_id = ?", values
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Task not found")
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user["id"]),
            ).fetchone()
        return _serialize_task(row)

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: int, request: Request) -> Response:
        user = authenticated_user(request)
        with connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user["id"])
            )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found")
        return Response(status_code=204)

    return app


def _serialize_task(row: sqlite3.Row) -> dict:
    return {
        **dict(row),
        "due_date": row["due_date"] or None,
        "completed": bool(row["completed"]),
    }


app = create_app()
