import sqlite3
from datetime import date

from fastapi.testclient import TestClient

from backend.app.main import create_app


def register(client: TestClient, email: str = "ada@example.com") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"name": "Ada Lovelace", "email": email, "password": "secure-pass-123"},
    )
    assert response.status_code == 201
    return response.json()


def test_authentication_and_user_owned_task_crud(tmp_path):
    app = create_app(tmp_path / "test.db")
    client = TestClient(app)
    other_client = TestClient(app)

    assert client.get("/api/tasks").status_code == 401
    user = register(client)
    assert user == {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"}
    assert client.get("/api/auth/me").json() == {"authenticated": True, "user": user}
    assert client.post(
        "/api/auth/register",
        json={"name": "Other", "email": "ADA@example.com", "password": "secure-pass-123"},
    ).status_code == 409

    created = client.post(
        "/api/tasks",
        json={
            "title": "  Plan the week  ",
            "description": "Review priorities",
            "due_date": date.today().isoformat(),
            "priority": "high",
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["title"] == "Plan the week"
    assert task["description"] == "Review priorities"
    assert task["completed"] is False

    assert other_client.post(
        "/api/auth/register",
        json={"name": "Grace Hopper", "email": "grace@example.com", "password": "other-pass-123"},
    ).status_code == 201
    assert other_client.get("/api/tasks").json() == []
    assert other_client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert other_client.patch(
        f"/api/tasks/{task['id']}", json={"title": "Not yours"}
    ).status_code == 404
    assert other_client.delete(f"/api/tasks/{task['id']}").status_code == 404

    completed = client.patch(f"/api/tasks/{task['id']}", json={"completed": True})
    assert completed.status_code == 200
    assert completed.json()["completed"] is True
    cleared_due_date = client.patch(f"/api/tasks/{task['id']}", json={"due_date": None})
    assert cleared_due_date.status_code == 200
    assert cleared_due_date.json()["due_date"] is None
    assert len(client.get("/api/tasks?status=completed").json()) == 1
    assert client.get("/api/tasks?status=pending").json() == []

    updated = client.patch(f"/api/tasks/{task['id']}", json={"title": "Plan my week"})
    assert updated.status_code == 200
    assert updated.json()["title"] == "Plan my week"
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_login_logout_and_password_hash_storage(tmp_path):
    database = tmp_path / "test.db"
    client = TestClient(create_app(database))
    register(client)

    with sqlite3.connect(database) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE email = ?", ("ada@example.com",)
        ).fetchone()[0]
    assert password_hash.startswith("pbkdf2_sha256$")
    assert password_hash != "secure-pass-123"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() == {"authenticated": False, "user": None}
    assert client.get("/api/tasks").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "missing@example.com", "password": "secure-pass-123"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": "incorrect-password"},
    ).status_code == 401

    logged_in = client.post(
        "/api/auth/login",
        json={"email": "ADA@example.com", "password": "secure-pass-123"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["email"] == "ada@example.com"
    assert client.get("/api/tasks").status_code == 200


def test_signed_session_survives_app_restart(tmp_path):
    database = tmp_path / "test.db"
    client = TestClient(create_app(database))
    user = register(client)
    session_cookie = client.cookies.get("daymark_session")

    restarted_client = TestClient(create_app(database))
    restarted_client.cookies.set("daymark_session", session_cookie)
    assert restarted_client.get("/api/auth/me").json() == {
        "authenticated": True,
        "user": user,
    }


def test_validation_and_legacy_tasks_migrate_to_first_account(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'medium',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (title, created_at, updated_at)
            VALUES ('Existing task', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )

    client = TestClient(create_app(database))
    assert client.post(
        "/api/auth/register",
        json={"name": "Invalid", "email": "not-an-email", "password": "secure-pass-123"},
    ).status_code == 422
    assert client.post(
        "/api/auth/register",
        json={"name": "Short password", "email": "short@example.com", "password": "short"},
    ).status_code == 422
    assert client.post("/api/tasks", json={"title": "Valid"}).status_code == 401
    register(client)
    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Existing task"


def test_task_validation_and_missing_update(tmp_path):
    client = TestClient(create_app(tmp_path / "test.db"))
    register(client)

    assert client.post("/api/tasks", json={"title": "   "}).status_code == 422
    assert client.post("/api/tasks", json={"title": "Valid", "priority": "urgent"}).status_code == 422
    assert client.patch("/api/tasks/99", json={"completed": True}).status_code == 404
    assert client.patch("/api/tasks/99", json={}).status_code == 400
