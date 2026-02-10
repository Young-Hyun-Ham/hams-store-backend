# app/routers/orders.py
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, conint
from psycopg2.extras import execute_values
import json

from app.db import get_conn

router = APIRouter(prefix="/orders", tags=["orders"])

class SelectedOptionIn(BaseModel):
    optionId: str
    valueKeys: List[str] = Field(default_factory=list)

class OrderItemIn(BaseModel):
    menuItemId: str
    qty: conint(gt=0) # type: ignore
    selectedOptions: List[SelectedOptionIn] = Field(default_factory=list)

class CreateOrderIn(BaseModel):
    customerId: Optional[str] = None
    customerNote: Optional[str] = None
    items: List[OrderItemIn]

# ---- 내부 헬퍼(검증) ----
def _fetch_menu_item(cur, menu_item_id: str):
    cur.execute("""select id, name, price, is_active from menu_items where id=%s""", (menu_item_id,))
    row = cur.fetchone()
    if not row: raise HTTPException(404, f"menu item not found: {menu_item_id}")
    if not row["is_active"]: raise HTTPException(400, f"menu item inactive: {menu_item_id}")
    return row

def _fetch_option_meta(cur, option_id: str):
    cur.execute("""select id, key, name, selection_type, is_required from menu_item_options where id=%s""", (option_id,))
    row = cur.fetchone()
    if not row: raise HTTPException(404, f"option not found: {option_id}")
    return row

def _assert_option_attached(cur, menu_item_id: str, option_id: str):
    cur.execute("""select 1 from menu_item_option_map where menu_item_id=%s and option_id=%s""", (menu_item_id, option_id))
    if not cur.fetchone():
        raise HTTPException(400, f"option not allowed for menu item (menuItemId={menu_item_id}, optionId={option_id})")

def _fetch_option_values(cur, option_id: str, value_keys: List[str]):
    if not value_keys: return []
    cur.execute("""
        select value_key, label, price_delta, is_active
        from menu_option_values
        where option_id=%s and value_key = any(%s)
    """, (option_id, value_keys))
    rows = cur.fetchall() or []
    found = {r["value_key"] for r in rows}
    missing = [k for k in value_keys if k not in found]
    if missing:
        raise HTTPException(400, f"invalid option values: {missing}")
    for r in rows:
        if not r["is_active"]:
            raise HTTPException(400, f"option value inactive: {r['value_key']}")
    by_key = {r["value_key"]: r for r in rows}
    return [by_key[k] for k in value_keys]


@router.post("")
def create_order(payload: CreateOrderIn):
    if not payload.items:
        raise HTTPException(400, "items is required")

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    insert into orders (customer_id, status, customer_note, total_amount)
                    values (%s, 'PLACED', %s, 0)
                    returning id::text as id, order_no, status, created_at
                """, (payload.customerId, payload.customerNote))
                order = cur.fetchone()
                order_id = order["id"]

                total_amount = 0

                for item in payload.items:
                    mi = _fetch_menu_item(cur, item.menuItemId)
                    unit_price = int(mi["price"])
                    qty = int(item.qty)

                    option_delta_sum = 0
                    option_rows = []  # snapshot rows

                    for so in item.selectedOptions:
                        opt = _fetch_option_meta(cur, so.optionId)
                        _assert_option_attached(cur, mi["id"], opt["id"])

                        if opt["selection_type"] == "single" and len(so.valueKeys) != 1:
                            raise HTTPException(400, f"option {opt['key']} is single-select")
                        if opt["selection_type"] == "multi" and len(so.valueKeys) < 1:
                            raise HTTPException(400, f"option {opt['key']} is multi-select")

                        vals = _fetch_option_values(cur, opt["id"], so.valueKeys)
                        for v in vals:
                            pd = int(v["price_delta"])
                            option_delta_sum += pd
                            option_rows.append((opt["key"], opt["name"], v["value_key"], v["label"], pd))

                    line_unit = unit_price + option_delta_sum
                    line_amount = line_unit * qty
                    total_amount += line_amount

                    cur.execute("""
                        insert into order_items (order_id, menu_item_id, name_snapshot, price_snapshot, qty, line_amount)
                        values (%s, %s, %s, %s, %s, %s)
                        returning id::text as id
                    """, (order_id, mi["id"], mi["name"], unit_price, qty, line_amount))
                    oi = cur.fetchone()
                    order_item_id = oi["id"]

                    if option_rows:
                        execute_values(cur, """
                            insert into order_item_options
                              (order_item_id, option_key, option_name, value_key, value_label, price_delta)
                            values %s
                        """, [(order_item_id, *r) for r in option_rows])

                cur.execute("""
                    update orders set total_amount=%s where id=%s
                    returning id::text as id, order_no, status, total_amount, created_at
                """, (total_amount, order_id))
                out = cur.fetchone()

                cur.execute("""
                    insert into order_status_logs(order_id, from_status, to_status, changed_by)
                    values (%s, null, 'PLACED', %s)
                """, (order_id, payload.customerId))

                return out
    finally:
        conn.close()


@router.get("/{order_id}")
def get_order(order_id: str):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select id::text, order_no, customer_id::text as customer_id, status, customer_note,
                           total_amount, created_at, accepted_at, completed_at, canceled_at
                    from orders where id=%s
                """, (order_id,))
                order = cur.fetchone()
                if not order:
                    raise HTTPException(404, "order not found")

                # ✅ 여기서 id는 uuid로 유지하고, order_id만 text로 바꿔도 됨
                cur.execute("""
                    select id, order_id::text as order_id, menu_item_id::text as menu_item_id,
                           name_snapshot, price_snapshot, qty, line_amount
                    from order_items where order_id=%s
                """, (order_id,))
                items = cur.fetchall() or []

                # ✅ item_ids는 uuid 리스트가 됨
                item_ids = [it["id"] for it in items]
                options = []
                if item_ids:
                    cur.execute("""
                        select id::text, order_item_id::text as order_item_id,
                               option_key, option_name, value_key, value_label, price_delta
                        from order_item_options
                        where order_item_id = any(%s::uuid[])
                    """, (item_ids,))
                    options = cur.fetchall() or []

                # ✅ 응답 JSON용: items의 id를 text로 변환
                for it in items:
                    it["id"] = str(it["id"])

                return {"order": order, "items": items, "itemOptions": options}
    finally:
        conn.close()

@router.get("")
def list_orders(customerId: Optional[str] = None, limit: int = 30):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if customerId:
                    cur.execute("""
                        select id::text, order_no, status, total_amount, created_at
                        from orders
                        where customer_id=%s
                        order by created_at desc
                        limit %s
                    """, (customerId, limit))
                else:
                    cur.execute("""
                        select id::text, order_no, status, total_amount, created_at
                        from orders
                        order by created_at desc
                        limit %s
                    """, (limit,))
                return {"orders": cur.fetchall() or []}
    finally:
        conn.close()


@router.post("/{order_id}/cancel")
def cancel_order(order_id: str, customerId: Optional[str] = None):
    """
    손님 취소: PLACED까지만 허용 (정책은 바꿀 수 있음)
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""select status, customer_id::text as customer_id from orders where id=%s""", (order_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "order not found")
                if customerId and row["customer_id"] != customerId:
                    raise HTTPException(403, "not your order")

                if row["status"] != "PLACED":
                    raise HTTPException(400, f"cannot cancel in status={row['status']}")

                cur.execute("""
                    update orders set status='CANCELED', canceled_at=now()
                    where id=%s
                    returning id::text, order_no, status, canceled_at
                """, (order_id,))
                out = cur.fetchone()

                cur.execute("""
                    insert into order_status_logs(order_id, from_status, to_status, changed_by)
                    values (%s, 'PLACED', 'CANCELED', %s)
                """, (order_id, customerId))

                return out
    finally:
        conn.close()
