from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def test_streamlit_entrypoint_is_a_thin_compatibility_composition_root() -> None:
    path = ROOT / "app.py"
    assert len(path.read_text(encoding="utf-8").splitlines()) < 300
    assert "streamlit_app" in path.read_text(encoding="utf-8")


def test_domain_layer_has_no_framework_network_config_or_persistence_imports() -> None:
    forbidden = (
        "streamlit", "sqlite3", "requests", "urllib.request", "config"
    )
    for path in (ROOT / "equity_research" / "domain").glob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(forbidden), (path.name, imported)


def test_application_layer_does_not_import_streamlit() -> None:
    for path in (ROOT / "equity_research" / "application").glob("*.py"):
        assert not any(
            imported.startswith("streamlit") for imported in _imports(path)
        )


def test_streamlit_renderer_uses_registry_instead_of_constructing_providers() -> None:
    path = ROOT / "equity_research" / "presentation" / "streamlit_app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "resolve_research_providers" in constructed
    assert not constructed & {
        "CodexLocalProvider",
        "OpenAIResponsesProvider",
        "OpenAICompatibleProvider",
        "UnavailableLlmProvider",
    }


def test_primary_navigation_renders_only_the_selected_route() -> None:
    path = ROOT / "equity_research" / "presentation" / "streamlit_app.py"
    source = path.read_text(encoding="utf-8")
    assert '["Decision", "Research", "Evidence", "Audit"]' in source
    assert 'if research_view == "Decision"' in source
    assert 'elif research_view == "Research"' in source
    assert 'elif research_view == "Evidence"' in source
    assert "st.tabs(" not in source
    assert 'st.markdown("### Independent Research QA")' in source
    assert "@st.cache_resource(ttl=30, show_spinner=False)" in source
