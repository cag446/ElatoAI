#!/usr/bin/env python3
"""Corre bridge.py con state.db aislada y puertos de prueba (NO toca producción).

Lo usa tests/test_barge_in.py, pero se puede correr a mano:

    cd ~/Proyectos/experimentos/hermes-bridge
    HERMES_API_KEY=<key> .venv/bin/python3 tests/run_bridge_isolated.py

Aísla:
  - puertos:   BRIDGE_WS_PORT=8100 / BRIDGE_HTTP_PORT=3100 (prod usa 8000/3000)
  - historial: state.db propia en /tmp/hermes-bridge-test/state.db
               (~/.hermes/state.db NO se toca)
Lo demás es el bridge real, con el mismo código de bridge.py.
"""

import os
import pathlib
import runpy
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

TEST_DB = os.environ.get("BRIDGE_TEST_STATE_DB", "/tmp/hermes-bridge-test/state.db")
pathlib.Path(TEST_DB).parent.mkdir(parents=True, exist_ok=True)

# Schema mínimo de state.db (sessions + messages): alcanza para VoiceHistory.
conn = sqlite3.connect(TEST_DB)
conn.executescript("""
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT,
    started_at REAL,
    ended_at REAL,
    title TEXT,
    message_count INTEGER DEFAULT 0,
    user_id TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    role TEXT,
    content TEXT,
    timestamp REAL
);
""")
conn.commit()
conn.close()

os.environ["BRIDGE_WS_PORT"] = os.environ.get("BRIDGE_TEST_WS_PORT", "8100")
os.environ["BRIDGE_HTTP_PORT"] = os.environ.get("BRIDGE_TEST_HTTP_PORT", "3100")

import voice_history  # noqa: E402  (después de fijar el entorno)

voice_history.STATE_DB = TEST_DB  # el historial de prueba no ensucia el real

print(f"[test-bridge] state.db aislada: {TEST_DB}", flush=True)
print(f"[test-bridge] WS {os.environ['BRIDGE_WS_PORT']} / HTTP {os.environ['BRIDGE_HTTP_PORT']}",
      flush=True)

runpy.run_path(str(REPO / "bridge.py"), run_name="__main__")
