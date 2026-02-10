from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.fcm import send_fcm_to_tokens

router = APIRouter(prefix="/orders", tags=["orders"])


class AcceptOrderIn(BaseModel):
    ownerId: str  # 임시: 로그인 붙으면 토큰에서 꺼내기
    message: Optional[str] = None  # 손님에게 보낼 문구 커스텀(옵션)


class AcceptOrderOut(BaseModel):
    orderId: str
    orderNo: int
    status: str
    acceptedAt: str
    notified: dict


@router.post("/{order_id}/accept", response_model=AcceptOrderOut)
def accept_order(order_id: str, payload: AcceptOrderIn):
    """
    사장님 접수:
    - orders.status=ACCEPTED, accepted_at=now()
    - order_status_logs 추가
    - 손님 devices의 fcm_token으로 "조리가 시작되었습니다" 발송
    - notification_logs 기록
    """
    # 1) DB에서 주문/손님 토큰 확보 + 상태 업데이트는 트랜잭션으로
    conn = get_conn()
    customer_id = None
    order_no = None
    accepted_at = None
    tokens: list[str] = []

    # 로그에 쓸 푸시 컨텐츠(기본)
    title = "임진매운갈비"
    body = payload.message or "조리가 시작되었습니다! 잠시만 기다려 주세요 😊"

    # data payload (앱에서 딥링크/상세열기용)
    data_payload = {
        "type": "order_status",
        "orderId": order_id,
        "nextStatus": "ACCEPTED",
    }

    try:
        with conn:
            with conn.cursor() as cur:
                # 주문 존재 + 현재 상태 확인 (중복 접수 방지)
                cur.execute(
                    """
                    select id::text as id, order_no, status, customer_id::text as customer_id
                    from orders
                    where id = %s
                    """,
                    (order_id,),
                )
                order = cur.fetchone()
                if not order:
                    raise HTTPException(status_code=404, detail="order not found")

                prev_status = order["status"]
                customer_id = order["customer_id"]
                order_no = int(order["order_no"])

                if prev_status in ("CANCELED", "COMPLETED"):
                    raise HTTPException(status_code=400, detail=f"cannot accept order in status={prev_status}")

                if prev_status == "ACCEPTED":
                    # 멱등 처리: 이미 접수됨(원하면 토큰 재발송 옵션도 가능)
                    cur.execute(
                        "select accepted_at from orders where id=%s",
                        (order_id,),
                    )
                    row = cur.fetchone()
                    accepted_at = str(row["accepted_at"]) if row and row["accepted_at"] else ""
                    return {
                        "orderId": order_id,
                        "orderNo": order_no,
                        "status": "ACCEPTED",
                        "acceptedAt": accepted_at,
                        "notified": {"skipped": True, "reason": "already accepted"},
                    }

                # status 업데이트
                cur.execute(
                    """
                    update orders
                    set status='ACCEPTED', accepted_at=now()
                    where id=%s
                    returning accepted_at
                    """,
                    (order_id,),
                )
                row = cur.fetchone()
                accepted_at = str(row["accepted_at"])

                # 상태 로그
                cur.execute(
                    """
                    insert into order_status_logs (order_id, from_status, to_status, changed_by)
                    values (%s, %s, 'ACCEPTED', %s)
                    """,
                    (order_id, prev_status, payload.ownerId),
                )

                # 손님 토큰 조회 (활성 device만)
                if customer_id:
                    cur.execute(
                        """
                        select fcm_token
                        from devices
                        where user_id = %s and is_active = true
                        """,
                        (customer_id,),
                    )
                    tokens = [r["fcm_token"] for r in (cur.fetchall() or []) if r.get("fcm_token")]

                # notification_logs에 우선 queued로 기록(발송 성공/실패는 아래에서 업데이트)
                cur.execute(
                    """
                    insert into notification_logs
                      (order_id, user_id, channel, title, body, payload, send_status)
                    values
                      (%s, %s, 'fcm', %s, %s, %s::jsonb, 'queued')
                    returning id::text as id
                    """,
                    (order_id, customer_id, title, body, __import__("json").dumps(data_payload)),
                )
                notif = cur.fetchone()
                notif_id = notif["id"]

        # 2) 트랜잭션 커밋 이후 FCM 발송 (네트워크 호출은 DB 트랜잭션 밖에서)
        notified = {"tokens": len(tokens), "success": 0, "failure": 0}

        if tokens:
            resp = send_fcm_to_tokens(tokens=tokens, title=title, body=body, data=data_payload)
            notified.update({"success": resp["success"], "failure": resp["failure"]})

            # 3) notification_logs 결과 업데이트
            conn2 = get_conn()
            try:
                with conn2:
                    with conn2.cursor() as cur2:
                        if resp["failure"] == 0:
                            cur2.execute(
                                """
                                update notification_logs
                                set send_status='sent', sent_at=now(), error_message=null
                                where id=%s
                                """,
                                (notif_id,),
                            )
                        else:
                            # 실패 사유 일부만 저장(너무 길어질 수 있음)
                            err = str(resp["responses"][:3])
                            cur2.execute(
                                """
                                update notification_logs
                                set send_status='failed', error_message=%s
                                where id=%s
                                """,
                                (err, notif_id),
                            )
            finally:
                conn2.close()
        else:
            # 토큰 없으면 실패로 볼지/스킵으로 볼지 정책 선택 가능 (여기선 failed로 기록)
            conn2 = get_conn()
            try:
                with conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            """
                            update notification_logs
                            set send_status='failed', error_message=%s
                            where order_id=%s and user_id=%s and channel='fcm' and send_status='queued'
                            """,
                            ("no active fcm tokens", order_id, customer_id),
                        )
            finally:
                conn2.close()

        return {
            "orderId": order_id,
            "orderNo": order_no,
            "status": "ACCEPTED",
            "acceptedAt": accepted_at,
            "notified": notified,
        }

    finally:
        conn.close()
