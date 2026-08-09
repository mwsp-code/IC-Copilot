from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_audit_route_renders_publication_readiness_without_route_crash() -> None:
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    assert not app.exception
    app.radio[0].set_value("Audit")
    app = app.run()

    assert not app.exception
    assert any(
        "Publication readiness" in str(item.value)
        for item in app.markdown
    )
    assert not any(
        "Technical evidence references" in str(item.value)
        for item in app.markdown
    )
