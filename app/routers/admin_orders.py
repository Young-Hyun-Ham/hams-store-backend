# app/routers/admin_orders.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json

from app.db import get_conn

from app.fcm import send_push_to_tokens

router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])

@router.get("")
def admin_list_orders(status: str | None = None, limit: int = 50):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("""
                        select id::text, order_no, customer_id::text as customer_id, status, total_amount, created_at
                        from orders
                        where status=%s
                        order by created_at desc
                        limit %s
                    """, (status, limit))
                else:
                    cur.execute("""
                        select id::text, order_no, customer_id::text as customer_id, status, total_amount, created_at
                        from orders
                        order by created_at desc
                        limit %s
                    """, (limit,))
                return {"orders": cur.fetchall() or []}
    finally:
        conn.close()


class AcceptIn(BaseModel):
    ownerId: str
    message: str | None = None

@router.post("/{order_id}/accept")
def admin_accept(order_id: str, payload: AcceptIn):
    title = "임진매운갈비"
    body = payload.message or "조리가 시작되었습니다! 잠시만 기다려 주세요 😊"
    data_payload = {"type": "order_status", "orderId": order_id, "nextStatus": "ACCEPTED"}

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select status, customer_id::text as customer_id, order_no
                    from orders where id=%s
                """, (order_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "order not found")

                prev = row["status"]
                if prev in ("CANCELED", "COMPLETED"):
                    raise HTTPException(400, f"cannot accept in status={prev}")
                if prev == "ACCEPTED":
                    return {"ok": True, "status": "ACCEPTED", "skipped": True}

                # 1) 주문 상태 업데이트
                cur.execute("""
                    update orders set status='ACCEPTED', accepted_at=now()
                    where id=%s
                    returning id::text, order_no, status, accepted_at
                """, (order_id,))
                out = cur.fetchone()

                # 2) 상태 로그
                cur.execute("""
                    insert into order_status_logs(order_id, from_status, to_status, changed_by)
                    values (%s, %s, 'ACCEPTED', %s)
                """, (order_id, prev, payload.ownerId))

                # 3) notification_logs 먼저 queued로 기록
                cur.execute("""
                    insert into notification_logs(order_id, user_id, channel, title, body, payload, send_status)
                    values (%s, %s, 'fcm', %s, %s, %s::jsonb, 'queued')
                    returning id::text as id
                """, (order_id, row["customer_id"], title, body, json.dumps(data_payload)))
                noti = cur.fetchone()
                noti_id = noti["id"]

                # 4) 고객 devices 토큰 조회
                cur.execute("""
                    select fcm_token
                    from devices
                    where user_id=%s and is_active=true
                    order by last_seen_at desc nulls last
                    limit 20
                """, (row["customer_id"],))
                tokens = [r["fcm_token"] for r in (cur.fetchall() or []) if r.get("fcm_token")]

                # 5) 실제 FCM 발송
                try:
                    success_count, results = send_push_to_tokens(
                        tokens=tokens,
                        title=title,
                        body=body,
                        data={k: str(v) for k, v in data_payload.items()},
                    )

                    # 6) notification_logs 업데이트 (sent/failed)
                    if tokens and success_count > 0:
                        cur.execute("""
                            update notification_logs
                            set send_status='sent', sent_at=now(), error_message=null
                            where id=%s
                        """, (noti_id,))
                    else:
                        cur.execute("""
                            update notification_logs
                            set send_status='failed',
                                error_message=%s
                            where id=%s
                        """, (json.dumps({"tokens": len(tokens), "results": results})[:4000], noti_id))

                except Exception as e:
                    # FCM 자체 실패
                    cur.execute("""
                        update notification_logs
                        set send_status='failed',
                            error_message=%s
                        where id=%s
                    """, (str(e)[:4000], noti_id))

                # out에는 status=ACCEPTED 포함 → 고객 주문상세에서도 그대로 보임
                return {
                    **out,
                    "push": {"tokens": len(tokens)}
                }
    finally:
        conn.close()


class CompleteIn(BaseModel):
    ownerId: str

@router.post("/{order_id}/complete")
def admin_complete(order_id: str, payload: CompleteIn):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""select status from orders where id=%s""", (order_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "order not found")

                prev = row["status"]
                if prev in ("CANCELED", "COMPLETED"):
                    raise HTTPException(400, f"cannot complete in status={prev}")

                cur.execute("""
                    update orders set status='COMPLETED', completed_at=now()
                    where id=%s
                    returning id::text, order_no, status, completed_at
                """, (order_id,))
                out = cur.fetchone()

                cur.execute("""
                    insert into order_status_logs(order_id, from_status, to_status, changed_by)
                    values (%s, %s, 'COMPLETED', %s)
                """, (order_id, prev, payload.ownerId))

                return out
    finally:
        conn.close()
