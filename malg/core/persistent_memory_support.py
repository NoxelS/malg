"""Scoped, durable NOOA memory for MALG research agents.

Each agent type receives an independent SQLite database.  The database path is
selected by the host, not generated code: Compose mounts a named volume at
``/memory`` and supplies ``MALG_MEMORY_DIRECTORY``.  This POC deliberately
uses only explicit memory operations; it does not inject memories, auto-write
events, or run reflection in the background.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

from nooa import Agent  # type: ignore[attr-defined]  # NOOA re-exports Agent dynamically.
from nooa_memory import (
    ForgetPolicy,
    MemoryConfig,
    MemoryManager,
    MemoryToolsMixin,
    ReflectionPolicy,
    SpontaneousConfig,
    WritePolicy,
)

from malg.utils.console_progress import ConsoleProgress


class PersistentMemorySupport(MemoryToolsMixin, Agent):
    """Give one agent type explicit long-term memory in its own SQLite file.

    Subclasses must set a stable, lowercase ``memory_scope``.  A scope selects
    both the SQLite file and NOOA owner, preventing one agent type from
    recalling another type's research in this proof of concept.
    """

    memory_scope: ClassVar[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Construct the NOOA agent and install its scoped memory manager."""
        super().__init__(*args, **kwargs)
        scope = self._validated_memory_scope()
        self._memory_progress: ConsoleProgress | None = None
        directory = Path(os.environ.get("MALG_MEMORY_DIRECTORY", "/tmp/malg-memory"))
        directory.mkdir(parents=True, exist_ok=True)
        MemoryManager.install(
            self,
            config=MemoryConfig(
                enabled=True,
                path=str(directory / f"{scope}.sqlite"),
                owner=scope,
                spontaneous=SpontaneousConfig(enabled=False),
                reflection=ReflectionPolicy(enabled=False),
                write=WritePolicy(on_events=()),
                forget=ForgetPolicy(enabled=False),
            ),
        )

    def _set_memory_progress(self, progress: ConsoleProgress) -> None:
        """Connect host-owned console reporting without exposing it to the agent."""
        self._memory_progress = progress

    def remember(
        self,
        content: str,
        *,
        type: str = "info",
        importance: str = "MEDIUM",
        tags: list[str] | None = None,
        title: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Save durable scoped memory and emit safe host-side progress metadata."""
        memory_id = cast(
            str,
            super().remember(
                content,
                type=type,
                importance=importance,
                tags=tags,
                title=title,
                references=references,
            ),
        )
        self._report_memory("saved", memory_id=memory_id)
        return memory_id

    def recall(self, query: str, k: int = 5, owner: str | None = None) -> list[Any]:
        """Load related scoped memories and report only their result count."""
        memories = cast(list[Any], super().recall(query, k=k, owner=owner))
        self._report_memory("recall", count=len(memories))
        return memories

    def search(self, query: str, k: int = 5, owner: str | None = None) -> list[Any]:
        """Search scoped memories and report only their result count."""
        memories = cast(list[Any], super().search(query, k=k, owner=owner))
        self._report_memory("search", count=len(memories))
        return memories

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        importance: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        references: list[str] | None = None,
    ) -> bool:
        """Refine scoped memory and report whether the target was found."""
        updated = bool(
            super().update_memory(
                memory_id,
                content=content,
                importance=importance,
                type=type,
                tags=tags,
                status=status,
                references=references,
            )
        )
        self._report_memory("updated", memory_id=memory_id, found=updated)
        return updated

    def forget(self, memory_id: str) -> bool:
        """Archive scoped memory and report whether the target was found."""
        archived = bool(super().forget(memory_id))
        self._report_memory("archived", memory_id=memory_id, found=archived)
        return archived

    def associate(self, a_id: str, b_id: str, relation: str = "related") -> None:
        """Associate two scoped memories without logging their contents or IDs."""
        super().associate(a_id, b_id, relation)
        self._report_memory("associated")

    def _report_memory(
        self,
        operation: str,
        *,
        memory_id: str | None = None,
        count: int | None = None,
        found: bool | None = None,
    ) -> None:
        """Send bounded lifecycle metadata to the attached host progress reporter."""
        progress = self._memory_progress
        if progress is None:
            return
        scope = self._validated_memory_scope()
        if operation == "saved" and memory_id is not None:
            progress.memory_saved(scope, memory_id)
        elif operation in {"recall", "search"} and count is not None:
            progress.memory_loaded(scope, operation, count)
        elif operation == "updated" and memory_id is not None and found is not None:
            progress.memory_updated(scope, memory_id, found)
        elif operation == "archived" and memory_id is not None and found is not None:
            progress.memory_archived(scope, memory_id, found)
        elif operation == "associated":
            progress.memory_associated(scope)

    @classmethod
    def _validated_memory_scope(cls) -> str:
        """Return the stable storage scope, rejecting accidental shared defaults."""
        scope = getattr(cls, "memory_scope", None)
        if not isinstance(scope, str) or not scope:
            raise TypeError("PersistentMemorySupport subclasses must define memory_scope.")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in scope):
            raise ValueError(
                "memory_scope must contain only lowercase letters, digits, and hyphens."
            )
        return scope

    def remember_source(
        self,
        summary: str,
        source_url: str,
        *,
        dataset_code: str | None = None,
        tags: list[str] | None = None,
        importance: str = "MEDIUM",
    ) -> str:
        """Save a reusable research finding with its direct source URL.

        Use only for a concise sourced finding that was verified during this
        task.  ``dataset_code`` is required for Eurostat findings when known;
        the text stays self-contained so later recall retains its provenance.
        """
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be an absolute HTTP(S) URL.")
        source_details = f"Source: {source_url}"
        if dataset_code:
            source_details = f"{source_details}\nDataset: {dataset_code}"
        return self.remember(
            f"{summary.strip()}\n\n{source_details}",
            type="info",
            importance=importance,
            tags=tags,
            title=dataset_code,
        )


def close_persistent_memory(agent: PersistentMemorySupport) -> None:
    """Close an agent's SQLite connection outside generated-agent capabilities."""
    manager = getattr(agent, "_memory", None)
    if isinstance(manager, MemoryManager):
        manager.uninstall()
