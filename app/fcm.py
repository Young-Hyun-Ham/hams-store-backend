# app/fcm.py
import os
import json
from typing import List, Dict, Any, Optional

import firebase_admin
from firebase_admin import credentials, messaging

def _init_firebase():
    if firebase_admin._apps:
        return

    path = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not path:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is required for FCM")

    cred = credentials.Certificate(path)
    firebase_admin.initialize_app(cred)

def send_fcm_to_tokens(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    tokens: fcm registration tokens
    data: FCM data payload (string map만 허용)
    """
    if not tokens:
        return {"ok": True, "sent": 0, "failed": 0, "results": []}

    _init_firebase()

    # FCM data는 string만 허용
    safe_data = {k: str(v) for k, v in (data or {}).items()}

    msg = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data=safe_data,
    )

    resp = messaging.send_each_for_multicast(msg)

    results = []
    for idx, r in enumerate(resp.responses):
        results.append({
            "token": tokens[idx],
            "success": r.success,
            "message_id": getattr(r, "message_id", None),
            "exception": str(r.exception) if r.exception else None,
        })

    return {
        "ok": True,
        "sent": resp.success_count,
        "failed": resp.failure_count,
        "results": results,
    }
