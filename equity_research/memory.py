from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .idea_engine import expected_value
from .models import TradeIdea
from .research_store import ResearchStore


class IdeaMemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.IDEA_MEMORY_PATH
        self.store = ResearchStore()
        self.store.migrate_idea_memory(self.path)

    def list_records(self) -> list[dict]:
        return self.store.list_idea_records()

    def save_idea(self, ticker: str, idea: TradeIdea, note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.save_idea_record(
            {
                "idea_id": idea.idea_id,
                "ticker": ticker.upper(),
                "saved_at": now,
                "title": idea.title,
                "direction": idea.direction,
                "score": idea.score.total if idea.score else None,
                "market_capture": idea.market_capture.category if idea.market_capture else None,
                "expected_value_pct": (
                    round(value, 2) if (value := expected_value(idea.scenarios)) is not None else None
                ),
                "status": "Open",
                "note": note,
                "idea": asdict(idea),
                "post_mortem": {},
            }
        )

    def update_post_mortem(
        self,
        idea_id: str,
        outcome: str,
        realized_return_pct: float | None,
        lessons: str,
    ) -> None:
        self.store.update_idea_post_mortem(idea_id, outcome, realized_return_pct, lessons)
