import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

STATIC_DIR = Path(__file__).parent / "static"


def get_conn():
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres may not be ready yet, so retry (startup order isn't guaranteed)
    for attempt in range(30):
        try:
            with get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS books ("
                    "id SERIAL PRIMARY KEY, "
                    "name TEXT NOT NULL, "
                    "author TEXT NOT NULL, "
                    "review TEXT)"
                )
            break
        except psycopg.OperationalError as e:
            print(f"DB not ready (attempt {attempt + 1}): {e}", flush=True)
            time.sleep(2)
    yield


app = FastAPI(title="Books", lifespan=lifespan)


class BookIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)
    review: Optional[str] = Field(default=None, max_length=2000)


@app.get("/healthz")
def healthz():
    # Liveness: the process is up. Doesn't touch the DB.
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # Readiness: can we actually serve requests (DB reachable)?
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


@app.get("/api/whoami")
def whoami():
    return {"pod": os.environ.get("HOSTNAME")}


@app.get("/api/books")
def list_books():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, author, review FROM books ORDER BY id"
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "author": r[2], "review": r[3]} for r in rows
    ]


@app.post("/api/books", status_code=201)
def add_book(book: BookIn):
    review = (book.review or "").strip() or None
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO books (name, author, review) VALUES (%s, %s, %s) RETURNING id",
            (book.name.strip(), book.author.strip(), review),
        ).fetchone()
    return {"id": row[0], "name": book.name, "author": book.author, "review": review}


@app.delete("/api/books/{book_id}")
def remove_book(book_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = %s", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="book not found")
    return {"deleted": book_id}


# Must stay last: a mount on "/" catches everything not matched above
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")