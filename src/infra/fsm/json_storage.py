"""
/**
 * @file: json_storage.py
 * @description: Файловое хранилище FSM-состояния для aiogram
 * @dependencies: aiogram.fsm.storage.base, json, asyncio
 * @created: 2026-05-05
 */
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class JsonFSMStorage(BaseStorage):
    def __init__(self, path: str = ".data/fsm_state.json") -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._store: dict[str, dict[str, Any]] = self._read_sync()

    def _read_sync(self) -> dict[str, dict[str, Any]]:
        try:
            if not self._path.exists():
                return {}
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
            return {}
        except Exception:
            return {}

    def _write_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._store, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _key_to_str(key: StorageKey) -> str:
        return (
            f"{key.bot_id}:{key.chat_id}:{key.user_id}:"
            f"{key.thread_id or 0}:{key.business_connection_id or ''}:{key.destiny}"
        )

    @staticmethod
    def _normalize_state(state: StateType = None) -> str | None:
        if state is None:
            return None
        if isinstance(state, str):
            return state
        return getattr(state, "state", None)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        storage_key = self._key_to_str(key)
        async with self._lock:
            row = dict(self._store.get(storage_key) or {"state": None, "data": {}})
            row["state"] = self._normalize_state(state)
            if row["state"] is None and not row.get("data"):
                self._store.pop(storage_key, None)
            else:
                self._store[storage_key] = row
            self._write_sync()

    async def get_state(self, key: StorageKey) -> str | None:
        storage_key = self._key_to_str(key)
        async with self._lock:
            row = self._store.get(storage_key) or {}
            state = row.get("state")
            return str(state) if isinstance(state, str) else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        storage_key = self._key_to_str(key)
        async with self._lock:
            row = dict(self._store.get(storage_key) or {"state": None, "data": {}})
            row["data"] = dict(data)
            if row.get("state") is None and not row["data"]:
                self._store.pop(storage_key, None)
            else:
                self._store[storage_key] = row
            self._write_sync()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        storage_key = self._key_to_str(key)
        async with self._lock:
            row = self._store.get(storage_key) or {}
            data = row.get("data")
            return dict(data) if isinstance(data, dict) else {}

    async def close(self) -> None:
        async with self._lock:
            self._write_sync()
