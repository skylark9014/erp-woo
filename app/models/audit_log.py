# app/models/audit_log.py
from typing import List, Dict, Any
import time
import threading

audit_log: List[Dict[str, Any]] = []
lock = threading.Lock()

def add_audit_entry(action: str, user: str, details: str):
    entry = {
        "action": action,
        "user": user,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "details": details,
    }
    with lock:
        audit_log.append(entry)

def get_audit_log() -> List[Dict[str, Any]]:
    with lock:
        return list(audit_log)
