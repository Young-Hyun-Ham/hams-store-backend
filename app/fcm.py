# app/fcm.py
import os
import json
from typing import List, Dict, Any, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, messaging

def _get_app():
    if firebase_admin._apps:
        return firebase_admin.get_app()

    path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

    if path:
        cred = credentials.Certificate(path)
        return firebase_admin.initialize_app(cred)

    if raw:
        cred = credentials.Certificate(json.loads(raw))
        return firebase_admin.initialize_app(cred)

    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_JSON")


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

    _get_app()

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


def send_push_to_tokens(tokens: List[str], title: str, body: str, data: Dict[str, str]) -> Tuple[int, List[Dict[str, Any]]]:
    """
    returns: (success_count, results[])
    results item: {token, ok, messageId?, error?}
    """
    if not tokens:
        return 0, []

    _get_app()

    # multicast (최대 500개)
    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        tokens=tokens,
    )

    resp = messaging.send_multicast(msg)
    results: List[Dict[str, Any]] = []
    for i, r in enumerate(resp.responses):
        if r.success:
            results.append({"token": tokens[i], "ok": True, "messageId": r.message_id})
        else:
            results.append({"token": tokens[i], "ok": False, "error": str(r.exception)})
    return resp.success_count, results
