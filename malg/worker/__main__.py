"""Run the durable research worker process."""

from __future__ import annotations

import asyncio

from malg.config import get_twenty_config, get_worker_config, load_settings
from malg.crm.client import TwentyClient
from malg.crm.publisher import CrmPublisher
from malg.database.session import make_engine, make_session_factory
from malg.worker.service import ResearchWorker, worker_loop


async def main() -> None:
    """Construct configured persistence and run until process cancellation."""
    settings = load_settings()
    engine = make_engine()
    sessions = make_session_factory(engine)
    client = TwentyClient(get_twenty_config(settings))
    worker = ResearchWorker(
        sessions,
        get_worker_config(settings),
        crm_publisher=CrmPublisher(sessions, get_twenty_config(settings), client),
        crm_client=client,
    )
    try:
        await worker_loop(worker)
    finally:
        await client.aclose()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
