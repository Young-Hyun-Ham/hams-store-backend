# app/routers/admin_notifications.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json

from app.db import get_conn
from app.fcm import send_fcm_to_tokens

router = APIRouter(prefix="/admin/notifications", tags=["admin-notifications"])

@router.get("")
def list_notifications(orderId: str | None = None, limit: int = 100):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if orderId:
                    cur.execute("""
                        select id::text, order_id::text as order_id, user_id::text as user_id,
                               channel, title, body, send_status, error_message, created_at, sent_at
                        from notification_logs
                        where order_id=%s
                        order by created_at desc
                        limit %s
                    """, (orderId, limit))
                else:
                    cur.execute("""
                        select id::text, order_id::text as order_id, user_id::text as user_id,
                               channel, title, body, send_status, error_message, created_at, sent_at
                        from notification_logs
                        order by created_at desc
                        limit %s
                    """, (limit,))
                return {"notifications": cur.fetchall() or []}
    finally:
        conn.close()


class DispatchOut(BaseModel):
    processed: int
    sent: int
    failed: int

@router.post("/dispatch", response_model=DispatchOut)
def dispatch_notifications(limit: int = 50):
    """
    notification_logs에서 send_status='queued'인 것들을 꺼내서
    devices의 active token들로 실제 FCM 전송하고
    send_status를 sent/failed 로 업데이트한다.
    """
    conn = get_conn()
    processed = 0
    sent_total = 0
    failed_total = 0

    try:
        with conn:
            with conn.cursor() as cur:
                # 1) queued 알림 가져오기
                cur.execute("""
                    select id::text as id,
                           order_id::text as order_id,
                           user_id::text as user_id,
                           title, body, payload
                    from notification_logs
                    where send_status='queued' and channel='fcm'
                    order by created_at asc
                    limit %s
                """, (limit,))
                rows = cur.fetchall() or []

                for n in rows:
                    processed += 1

                    # 2) 사용자 devices 토큰 조회
                    cur.execute("""
                        select fcm_token
                        from devices
                        where user_id=%s and is_active=true and fcm_token is not null and fcm_token <> ''
                    """, (n["user_id"],))
                    tokens = [r["fcm_token"] for r in (cur.fetchall() or [])]

                    if not tokens:
                        failed_total += 1
                        cur.execute("""
                            update notification_logs
                            set send_status='failed',
                                error_message=%s,
                                sent_at=now()
                            where id=%s
                        """, ("no active device tokens", n["id"]))
                        continue

                    # payload: jsonb -> dict
                    data_payload = n["payload"] or {}
                    if isinstance(data_payload, str):
                        try:
                            data_payload = json.loads(data_payload)
                        except:
                            data_payload = {}

                    # 3) FCM 발송
                    try:
                        resp = send_fcm_to_tokens(
                            tokens=tokens,
                            title=n["title"],
                            body=n["body"],
                            data={k: str(v) for k, v in (data_payload or {}).items()},
                        )

                        # 성공/실패 집계
                        if resp.get("failed", 0) == 0:
                            sent_total += 1
                            cur.execute("""
                                update notification_logs
                                set send_status='sent',
                                    error_message=null,
                                    sent_at=now()
                                where id=%s
                            """, (n["id"],))
                        else:
                            failed_total += 1
                            cur.execute("""
                                update notification_logs
                                set send_status='failed',
                                    error_message=%s,
                                    sent_at=now()
                                where id=%s
                            """, (json.dumps(resp.get("results", []))[:2000], n["id"]))
                    except Exception as e:
                        failed_total += 1
                        cur.execute("""
                            update notification_logs
                            set send_status='failed',
                                error_message=%s,
                                sent_at=now()
                            where id=%s
                        """, (str(e)[:2000], n["id"]))

        return DispatchOut(processed=processed, sent=sent_total, failed=failed_total)
    finally:
        conn.close()
