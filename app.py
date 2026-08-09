"""Thin Streamlit composition root.

The implementation lives in the presentation package so research/application
code can be imported and tested without importing Streamlit. Keeping this
facade preserves the historical ``streamlit run app.py`` and ``import app``
interfaces during the incremental migration.
"""

from __future__ import annotations

import sys

from equity_research.presentation import streamlit_app as _implementation


# Compatibility alias: callers that import ``app`` receive the presentation
# module itself, including legacy private helpers used by existing extensions.
# This also means test monkeypatches of ``app.st`` affect the live renderer.
sys.modules[__name__] = _implementation


if __name__ == "__main__":
    _implementation.main()
