"""Run the durable research worker process."""

from __future__ import annotations

import asyncio

from malg.config import get_icp_config, get_worker_config, load_settings
from malg.database.session import make_engine, make_session_factory
from malg.worker.service import ResearchWorker, worker_loop


async def main() -> None:
    """Construct configured persistence and run until process cancellation."""
    settings = load_settings()
    engine = make_engine()
    worker = ResearchWorker(
        make_session_factory(engine), get_worker_config(settings), get_icp_config(settings)
    )
    try:
        await worker_loop(worker)
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
