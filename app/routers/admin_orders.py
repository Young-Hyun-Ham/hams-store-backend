# app/routers/admin_orders.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json

from app.db import get_conn

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
    """
    여기서는 'DB상태 변경 + 로그 + notification_logs(queued)'까지만.
    웹 우선이므로 실제 FCM 발송은 나중에 붙이기 좋게 남겨둠.
    """
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

                cur.execute("""
                    update orders set status='ACCEPTED', accepted_at=now()
                    where id=%s
                    returning id::text, order_no, status, accepted_at
                """, (order_id,))
                out = cur.fetchone()

                cur.execute("""
                    insert into order_status_logs(order_id, from_status, to_status, changed_by)
                    values (%s, %s, 'ACCEPTED', %s)
                """, (order_id, prev, payload.ownerId))

                cur.execute("""
                    insert into notification_logs(order_id, user_id, channel, title, body, payload, send_status)
                    values (%s, %s, 'fcm', %s, %s, %s::jsonb, 'queued')
                """, (order_id, row["customer_id"], title, body, json.dumps(data_payload)))

                return out
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
