import os
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from pydantic import BaseModel


def get_conn():
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres may not be ready yet when the pod starts, so retry.
    # This is normal in k8s: startup order is NOT guaranteed.
    for attempt in range(30):
        try:
            with get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS notes ("
                    "id SERIAL PRIMARY KEY, text TEXT NOT NULL)"
                )
            break
        except psycopg.OperationalError as e:
            print(f"DB not ready (attempt {attempt + 1}): {e}", flush=True)
            time.sleep(2)
    yield


app = FastAPI(lifespan=lifespan)


class Note(BaseModel):
    text: str


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/whoami")
def whoami():
    # Handy to see load balancing across replicas
    return {"pod": os.environ.get("HOSTNAME")}


@app.post("/notes")
def create_note(note: Note):
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO notes (text) VALUES (%s) RETURNING id", (note.text,)
        ).fetchone()
    return {"id": row[0], "text": note.text}


@app.get("/notes")
def list_notes():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, text FROM notes ORDER BY id").fetchall()
    return [{"id": r[0], "text": r[1]} for r in rows]