from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping


@dataclass(frozen=True)
class RouteNavigationIntent:
    """A route change requested by a renderer and applied before widgets exist.

    Streamlit forbids mutating a widget's session-state key after that widget
    has been instantiated.  Renderers therefore stage navigation here; the
    composition root consumes it before creating the primary route radio.
    """

    destination: str
    origin: str = ""


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

    def request_route(self, destination: str, *, origin: str = "") -> None:
        """Stage a route change without mutating the route widget key."""

        if destination not in {"Decision", "Research", "Evidence", "Audit"}:
            raise ValueError(f"Unsupported research route: {destination}")
        self.values["_route_navigation_intent"] = RouteNavigationIntent(
            destination=destination,
            origin=origin,
        )

    def request_public_demo_landing(self) -> None:
        """Open a newly loaded public demo at its decision-first entry point."""

        self.request_route("Decision", origin="public_demo_load")

    def apply_pending_route(self) -> str:
        """Apply a staged route before the corresponding widget is created."""

        pending = self.values.pop("_route_navigation_intent", None)
        if isinstance(pending, RouteNavigationIntent):
            self.selected_route = pending.destination
        elif isinstance(pending, dict):
            destination = str(pending.get("destination") or "")
            if destination in {"Decision", "Research", "Evidence", "Audit"}:
                self.selected_route = destination
        elif isinstance(pending, str) and pending in {
            "Decision", "Research", "Evidence", "Audit",
        }:
            self.selected_route = pending
        return self.selected_route

    @property
    def result(self) -> Any:
        return self.values.get("result")

    @result.setter
    def result(self, value: Any) -> None:
        self.values["result"] = value

    @property
    def displayed_artifact_id(self) -> str:
        return str(self.values.get("_displayed_saved_artifact_id") or "")

    @displayed_artifact_id.setter
    def displayed_artifact_id(self, value: str) -> None:
        self.values["_displayed_saved_artifact_id"] = str(value or "")

    @property
    def displayed_result_origin(self) -> str:
        return str(self.values.get("_displayed_result_origin") or "frozen_demo")

    @displayed_result_origin.setter
    def displayed_result_origin(self, value: str) -> None:
        allowed = {
            "frozen_demo",
            "saved_live_research",
            "saved_research_draft",
            "live_not_saved",
        }
        if value not in allowed:
            raise ValueError(f"Unsupported displayed result origin: {value}")
        self.values["_displayed_result_origin"] = value

    @property
    def displayed_research_context(self) -> Any:
        """Non-secret saved-result context consumed by presentation routes."""

        return self.values.get("_displayed_research_context")

    @displayed_research_context.setter
    def displayed_research_context(self, value: Any) -> None:
        self.values["_displayed_research_context"] = value

    @property
    def selected_research_intent(self) -> Any:
        return self.values.get("_selected_research_launch_intent")

    @selected_research_intent.setter
    def selected_research_intent(self, value: Any) -> None:
        self.values["_selected_research_launch_intent"] = value

    def clear_selected_research_intent(self) -> None:
        self.values.pop("_selected_research_launch_intent", None)
        self.values.pop("_research_launch_repeat_confirmation", None)

    def consume(self, key: str, default: Any = None) -> Any:
        return self.values.pop(key, default)
