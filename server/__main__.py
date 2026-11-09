#!/usr/bin/env python3
"""Start the event server: python3 -m server"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from pathlib import Path
import signal

import uvicorn

from server.app import create_admin_app, create_team_app
from server.containers import IMAGE, Docker
from server.db import DATA_DIR, Database
from server.event import Event
from server.hub import Hub
from server.llm import MISSING_MESSAGE, azure_client


MAX_TEAM_MESSAGE_BYTES = 64 * 1024


class Server(uvicorn.Server):
    """Two servers share one process, so main() owns Ctrl-C instead of uvicorn."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(args: argparse.Namespace) -> None:
    db = Database(args.db)
    settings = db.settings()
    event = Event(
        db, Hub(), Docker(), azure_client(settings["azure_base_url"], settings["azure_api_key"]),
        team_port=args.team_port,
    )
    await event.startup()
    if event.client is None:
        print(f"WARNING: {MISSING_MESSAGE} Agents and chat will not work.")
    if event.docker_status != "ok":
        print(f"WARNING: sandbox image {IMAGE} is not available: {event.docker_status}")
        print(f"Build it with: docker build -t {IMAGE} -f sandbox/Dockerfile .")

    servers = [
        Server(uvicorn.Config(
            create_team_app(event), host=args.team_host, port=args.team_port, log_level="warning",
            ws_max_size=MAX_TEAM_MESSAGE_BYTES, timeout_graceful_shutdown=2,
        )),
        Server(uvicorn.Config(
            create_admin_app(event, port=args.admin_port), host="127.0.0.1", port=args.admin_port,
            log_level="warning", timeout_graceful_shutdown=2,
        )),
    ]

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop)

    print(f"Admin (this machine only): http://localhost:{args.admin_port}")
    print(f"Team site:                 http://localhost:{args.team_port}")
    print(f"Expose it with:            cloudflared tunnel --url http://localhost:{args.team_port}")
    ticker = asyncio.create_task(event.tick_forever())
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        ticker.cancel()
        for task in list(event._tasks):
            task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-host", default="127.0.0.1")
    parser.add_argument("--team-port", type=int, default=8000)
    parser.add_argument("--admin-port", type=int, default=8001)
    parser.add_argument("--db", type=Path, default=DATA_DIR / "event.sqlite3")
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
