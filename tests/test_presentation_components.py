from equity_research.presentation.components import (
    render_bounded_data_table,
    render_metric_cards,
    render_status_badge,
)


class _Column:
    def __init__(self, owner):
        self.owner = owner

    def metric(self, label, value):
        self.owner.metrics.append((label, value))


class _StreamlitDouble:
    def __init__(self):
        self.captions = []
        self.infos = []
        self.metrics = []
        self.column_widths = []
        self.frames = []

    def caption(self, value):
        self.captions.append(value)

    def info(self, value):
        self.infos.append(value)

    def columns(self, count):
        self.column_widths.append(count)
        return [_Column(self) for _ in range(count)]

    def dataframe(self, rows, **kwargs):
        self.frames.append((rows, kwargs))


def test_status_badge_never_relies_on_color_or_icon_alone() -> None:
    st = _StreamlitDouble()
    render_status_badge(st, "Independent Research QA", "Not completed")

    assert "Independent Research QA" in st.captions[0]
    assert "Not completed" in st.captions[0]


def test_metric_grid_never_places_more_than_four_items_in_a_row() -> None:
    st = _StreamlitDouble()
    render_metric_cards(st, [(f"Metric {i}", str(i)) for i in range(9)])

    assert st.column_widths == [4, 4, 1]
    assert len(st.metrics) == 9


def test_bounded_table_limits_rows_and_explains_the_limit() -> None:
    st = _StreamlitDouble()
    render_bounded_data_table(st, [{"value": i} for i in range(25)], max_rows=10)

    assert len(st.frames[0][0]) == 10
    assert "Showing 10 of 25" in st.captions[0]


def test_empty_table_explains_why_data_is_absent() -> None:
    st = _StreamlitDouble()
    render_bounded_data_table(
        st,
        [],
        empty_title="No admitted candidates",
        empty_reason="Discovery was not needed for this run.",
    )

    assert "Discovery was not needed" in st.infos[0]
