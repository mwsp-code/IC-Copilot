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


def test_aapl_model_valuation_resolves_inputs_without_generic_gap() -> None:
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    assert not app.exception
    app.radio[0].set_value("Research")
    app = app.run()
    route = next(item for item in app.radio if item.label == "Research section")
    route.set_value("Model & Valuation")
    app = app.run()

    assert not app.exception
    assert any(
        item.label == "How valuation inputs were resolved"
        for item in app.expander
    )
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.warning, *app.caption]
    )
    assert "EBITDA, net debt, shares, or currency normalization" not in rendered
    assert "Forward EBITDA proxy" in rendered
