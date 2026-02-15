# app/routers/devices.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from uuid import UUID

from app.db import get_conn

router = APIRouter(prefix="/devices", tags=["devices"])

class RegisterDeviceIn(BaseModel):
    userId: str
    platform: str  # 'web' | 'ios' | 'android'
    fcmToken: str

def _assert_uuid(v: str) -> str:
    try:
        UUID(v)
        return v
    except Exception:
        raise HTTPException(400, "userId must be uuid")

@router.post("/register")
def register_device(payload: RegisterDeviceIn):
    if payload.platform not in ("web", "ios", "android"):
        raise HTTPException(400, "platform must be web|ios|android")
    if not payload.fcmToken or not payload.fcmToken.strip():
        raise HTTPException(400, "fcmToken is required")

    user_id = _assert_uuid(payload.userId)

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # ✅ (로그인 없는 MVP) users 테이블에 없으면 "익명 유저"로 자동 생성해서 FK 만족
                # - 아래 insert 컬럼은 네 users 스키마에 맞게 조정 가능
                cur.execute("""
                    insert into users (id, role, name)
                    values (%s, 'customer', %s)
                    on conflict (id) do nothing
                    """, (user_id, f"guest-{user_id[:8]}"))

                # ✅ devices upsert (fcm_token unique 기준)
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
                """, (user_id, payload.platform, payload.fcmToken))
                return cur.fetchone()
    finally:
        conn.close()

class UnregisterDeviceIn(BaseModel):
    fcmToken: str

@router.post("/unregister")
def unregister_device(payload: UnregisterDeviceIn):
    if not payload.fcmToken or not payload.fcmToken.strip():
        raise HTTPException(400, "fcmToken is required")

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
