"""Live WebSocket connections for team pages and the admin page."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


SEND_TIMEOUT_SECONDS = 3.0
CLOSE_CODE_LINK_CHANGED = 4403


class Hub:
    def __init__(self) -> None:
        self.teams: dict[int, set[WebSocket]] = defaultdict(set)
        self.admins: set[WebSocket] = set()
        self.chats: dict[str, set[WebSocket]] = defaultdict(set)

    def add_team(self, team_id: int, socket: WebSocket) -> None:
        self.teams[team_id].add(socket)

    def remove_team(self, team_id: int, socket: WebSocket) -> None:
        self.teams[team_id].discard(socket)

    def add_chat(self, session: str, socket: WebSocket) -> None:
        self.chats[session].add(socket)

    def remove_chat(self, session: str, socket: WebSocket) -> None:
        self.chats[session].discard(socket)
        if not self.chats[session]:
            del self.chats[session]

    def add_admin(self, socket: WebSocket) -> None:
        self.admins.add(socket)

    def remove_admin(self, socket: WebSocket) -> None:
        self.admins.discard(socket)

    async def _send(self, group: set[WebSocket], socket: WebSocket, message: dict) -> None:
        try:
            await asyncio.wait_for(socket.send_json(message), SEND_TIMEOUT_SECONDS)
        except Exception:
            # A sleeping phone must not hold up anyone else; it resyncs on reconnect.
            group.discard(socket)

    async def _broadcast(self, group: set[WebSocket], message: dict, skip: WebSocket | None = None) -> None:
        await asyncio.gather(*(
            self._send(group, socket, message) for socket in list(group) if socket is not skip
        ))

    async def to_team(self, team_id: int, message: dict, skip: WebSocket | None = None) -> None:
        await self._broadcast(self.teams[team_id], message, skip)

    async def to_chat(self, session: str, message: dict) -> None:
        if session in self.chats:
            await self._broadcast(self.chats[session], message)

    async def to_admins(self, message: dict) -> None:
        await self._broadcast(self.admins, message)

    async def _close(self, sockets) -> None:
        for socket in list(sockets):
            try:
                await socket.close(code=CLOSE_CODE_LINK_CHANGED)
            except Exception:
                pass

    async def close_team(self, team_id: int) -> None:
        await self._close(self.teams.pop(team_id, ()))

    async def close_chats(self) -> None:
        for session in list(self.chats):
            await self._close(self.chats.get(session, ()))
