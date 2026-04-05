"""
Database operations for gmar_app — fixed version.
Re-exports from telegram_attacks.db so both apps share the same logic.
"""
import os
import sys

# Ensure scripts/ is on path
_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from telegram_attacks.db import (  # noqa: F401
    get_conn,
    ensure_schema,
    find_recent_existing_by_area,
    insert_attack,
    build_updated_record,
    diff_update_fields,
    update_attack,
)
