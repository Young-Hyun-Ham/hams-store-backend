# app/routers/users.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from uuid import UUID

from app.db import get_conn

router = APIRouter(prefix="/users", tags=["users"])

class UpsertGuestIn(BaseModel):
    id: str  # uuid string (customerId)
    name: str | None = None

@router.post("/guest")
def upsert_guest(payload: UpsertGuestIn):
    try:
        user_id = str(UUID(payload.id))
    except Exception:
        raise HTTPException(400, "id must be uuid")

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    insert into users (id, role, name)
                    values (%s, 'customer', %s)
                    on conflict (id) do update
                      set updated_at = now(),
                          name = coalesce(excluded.name, users.name)
                    returning id::text as id, role, name
                """, (user_id, payload.name))
                return cur.fetchone()
    finally:
        conn.close()
