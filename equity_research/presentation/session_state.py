from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping


@dataclass
class UiSessionState:
    """Typed adapter around the small amount of state owned by the UI."""

    values: MutableMapping[str, Any]

    @property
    def active_run_id(self) -> str:
        return str(self.values.get("hybrid_job_id") or self.values.get("active_run_id") or "")

    @active_run_id.setter
    def active_run_id(self, value: str) -> None:
        self.values["active_run_id"] = str(value or "")
        # Preserve the existing key while the legacy job manager is migrated.
        self.values["hybrid_job_id"] = str(value or "")

    @property
    def selected_route(self) -> str:
        value = str(self.values.get("research_view") or "Decision")
        return value if value in {"Decision", "Research", "Evidence", "Audit"} else "Decision"

    @selected_route.setter
    def selected_route(self, value: str) -> None:
        if value not in {"Decision", "Research", "Evidence", "Audit"}:
            raise ValueError(f"Unsupported research route: {value}")
        self.values["research_view"] = value

    @property
    def result(self) -> Any:
        return self.values.get("result")

    @result.setter
    def result(self, value: Any) -> None:
        self.values["result"] = value

    def consume(self, key: str, default: Any = None) -> Any:
        return self.values.pop(key, default)
