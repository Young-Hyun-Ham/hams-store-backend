# app/routers/devices.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_conn

router = APIRouter(prefix="/devices", tags=["devices"])

class RegisterDeviceIn(BaseModel):
    userId: str
    platform: str  # 'web' | 'ios' | 'android'
    fcmToken: str

@router.post("/register")
def register_device(payload: RegisterDeviceIn):
    if payload.platform not in ("web", "ios", "android"):
        raise HTTPException(400, "platform must be web|ios|android")

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # fcm_token unique 이라서 upsert 처리
                cur.execute("""
                    insert into devices (user_id, platform, fcm_token, is_active, last_seen_at)
                    values (%s, %s, %s, true, now())
                    on conflict (fcm_token)
                    do update set
                      user_id=excluded.user_id,
                      platform=excluded.platform,
                      is_active=true,
                      last_seen_at=now()
                    returning id::text, user_id::text as user_id, platform, fcm_token, is_active
                """, (payload.userId, payload.platform, payload.fcmToken))
                return cur.fetchone()
    finally:
        conn.close()

class UnregisterDeviceIn(BaseModel):
    fcmToken: str

@router.post("/unregister")
def unregister_device(payload: UnregisterDeviceIn):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    update devices set is_active=false, last_seen_at=now()
                    where fcm_token=%s
                    returning id::text, is_active
                """, (payload.fcmToken,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "token not found")
                return row
    finally:
        conn.close()
