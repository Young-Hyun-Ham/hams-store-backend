# app/routers/menu.py
from fastapi import APIRouter
from app.db import get_conn

router = APIRouter(prefix="/menu", tags=["menu"])

@router.get("")
def get_menu():
    """
    카테고리/메뉴/옵션을 한 번에 내려주는 손님용 메뉴판 API
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select id::text, name, sort_order, is_active
                    from menu_categories
                    where is_active=true
                    order by sort_order asc, name asc
                """)
                categories = cur.fetchall() or []

                cur.execute("""
                    select id::text, category_id::text as category_id, name, description, price, image_url,
                           sort_order, is_active
                    from menu_items
                    where is_active=true
                    order by sort_order asc, name asc
                """)
                items = cur.fetchall() or []

                # 옵션 정의/값
                cur.execute("""
                    select id::text, key, name, selection_type, is_required, sort_order
                    from menu_item_options
                    order by sort_order asc, name asc
                """)
                options = cur.fetchall() or []

                cur.execute("""
                    select id::text, option_id::text as option_id, value_key, label, price_delta, sort_order, is_active
                    from menu_option_values
                    where is_active=true
                    order by sort_order asc, label asc
                """)
                option_values = cur.fetchall() or []

                # 메뉴-옵션 매핑
                cur.execute("""
                    select menu_item_id::text as menu_item_id, option_id::text as option_id, sort_order
                    from menu_item_option_map
                    order by sort_order asc
                """)
                maps = cur.fetchall() or []

        return {
            "categories": categories,
            "items": items,
            "options": options,
            "optionValues": option_values,
            "itemOptionMap": maps,
        }
    finally:
        conn.close()


@router.get("/items/{item_id}")
def get_menu_item(item_id: str):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select id::text, category_id::text as category_id, name, description, price, image_url,
                           sort_order, is_active
                    from menu_items
                    where id=%s
                """, (item_id,))
                item = cur.fetchone()
                if not item:
                    return {"item": None}
                return {"item": item}
    finally:
        conn.close()
