from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket


class ChatConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms[session_id].append(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(session_id, [])
        if websocket in room:
            room.remove(websocket)
        if not room and session_id in self._rooms:
            del self._rooms[session_id]

    async def broadcast(self, session_id: str, payload: dict, sender: WebSocket | None = None) -> None:
        peers = list(self._rooms.get(session_id, []))
        if not peers:
            return
        raw = json.dumps(payload)
        tasks = []
        for peer in peers:
            if sender is not None and peer is sender:
                continue
            tasks.append(peer.send_text(raw))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class NotificationConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    @staticmethod
    def _key(role: str, user_id: str) -> str:
        return f"{role.lower().strip()}::{user_id.strip()}"

    async def connect(self, role: str, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[self._key(role, user_id)].append(websocket)

    def disconnect(self, role: str, user_id: str, websocket: WebSocket) -> None:
        key = self._key(role, user_id)
        clients = self._connections.get(key, [])
        if websocket in clients:
            clients.remove(websocket)
        if not clients and key in self._connections:
            del self._connections[key]

    async def push(self, role: str, user_id: str, payload: dict) -> None:
        key = self._key(role, user_id)
        clients = list(self._connections.get(key, []))
        if not clients:
            return
        raw = json.dumps(payload)
        await asyncio.gather(*(ws.send_text(raw) for ws in clients), return_exceptions=True)


class SignalingConnectionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, WebSocket]] = defaultdict(dict)
        self._logger = logging.getLogger("signaling_manager")

    async def connect(self, session_id: str, role: str, websocket: WebSocket) -> None:
        role_key = role.strip().lower()
        await websocket.accept()
        existing = self._sessions[session_id].get(role_key)
        if existing is not None and existing is not websocket:
            self._logger.warning("duplicate_peer_replaced", extra={"session_id": session_id, "role": role_key})
            try:
                await existing.close(code=4001, reason="Duplicate peer for role")
            except Exception:
                pass
        self._sessions[session_id][role_key] = websocket
        self._logger.info("peer_connected", extra={"session_id": session_id, "role": role_key})

    def disconnect(self, session_id: str, role: str, websocket: WebSocket) -> None:
        role_key = role.strip().lower()
        peers = self._sessions.get(session_id)
        if not peers:
            return
        if peers.get(role_key) is websocket:
            del peers[role_key]
            self._logger.info("peer_disconnected", extra={"session_id": session_id, "role": role_key})
        if not peers:
            self._sessions.pop(session_id, None)
            self._logger.info("session_cleaned", extra={"session_id": session_id})

    async def relay(self, session_id: str, role: str, payload: dict) -> None:
        role_key = role.strip().lower()
        peers = self._sessions.get(session_id, {})
        targets = [(target_role, ws) for target_role, ws in peers.items() if target_role != role_key]
        if not targets:
            return
        raw = json.dumps(payload)
        self._logger.info(
            "signal_relay",
            extra={
                "session_id": session_id,
                "from_role": role_key,
                "signal_type": payload.get("type", "unknown"),
            },
        )
        await asyncio.gather(*(ws.send_text(raw) for _, ws in targets), return_exceptions=True)


chat_manager = ChatConnectionManager()
notification_manager = NotificationConnectionManager()
signaling_manager = SignalingConnectionManager()
