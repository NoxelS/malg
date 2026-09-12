"""Scoped, durable NOOA memory for MALG research agents in PostgreSQL."""

from __future__ import annotations

from typing import Any, ClassVar, cast
from urllib.parse import urlparse

from nooa import Agent  # type: ignore[attr-defined]
from nooa_memory import (
    ForgetPolicy,
    MemoryConfig,
    MemoryManager,
    MemoryToolsMixin,
    ReflectionPolicy,
    SpontaneousConfig,
    WritePolicy,
)
from sqlalchemy.orm import Session, sessionmaker

from malg.database.memory import PostgresMemoryStore
from malg.database.session import make_engine, make_session_factory
from malg.utils.console_progress import ConsoleProgress


class PostgresMemoryManager(MemoryManager):
    """NOOA manager using MALG's shared PostgreSQL memory store."""

    def __init__(
        self,
        agent: Agent,
        config: MemoryConfig | None = None,
        *,
        session_factory: sessionmaker[Session],
        **kwargs: Any,
    ) -> None:
        self._session_factory = session_factory
        super().__init__(agent, config, **kwargs)

    def _make_store(self, agent: Agent) -> PostgresMemoryStore:
        """Create a store after validating the configured embedder dimension."""
        return PostgresMemoryStore(self._session_factory, embedding_dim=self.embedder.dim)


class PersistentMemorySupport(MemoryToolsMixin, Agent):
    """Give one agent type scoped durable memory in PostgreSQL.

    Callers may provide an existing ``session_factory`` or ``database_url``.
    The store owns no durable local files and uses ``memory_scope`` as owner.
    """

    memory_scope: ClassVar[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Construct the agent and install its PostgreSQL memory manager."""
        session_factory = cast(sessionmaker[Session] | None, kwargs.pop("session_factory", None))
        database_url = cast(str | None, kwargs.pop("database_url", None))
        super().__init__(*args, **kwargs)
        scope = self._validated_memory_scope()
        self._memory_progress: ConsoleProgress | None = None
        if session_factory is None:
            session_factory = make_session_factory(make_engine(database_url))
        PostgresMemoryManager.install(
            self,
            config=MemoryConfig(
                enabled=True,
                owner=scope,
                spontaneous=SpontaneousConfig(enabled=False),
                reflection=ReflectionPolicy(enabled=False),
                write=WritePolicy(on_events=()),
                forget=ForgetPolicy(enabled=False),
            ),
            session_factory=session_factory,
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
