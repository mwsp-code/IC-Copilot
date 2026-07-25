from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("app.py", "server.py")
SHELL_COMMAND_MARKERS = (
    "streamlit run ",
    "python server.py",
    "python app.py",
    "powershell ",
)


@pytest.mark.parametrize("filename", ENTRYPOINTS)
def test_python_entrypoint_compiles_without_shell_commands(filename: str) -> None:
    path = ROOT / filename
    source = path.read_text(encoding="utf-8")
    first_line = next((line.strip().lower() for line in source.splitlines() if line.strip()), "")

    assert not any(first_line.startswith(marker) for marker in SHELL_COMMAND_MARKERS), (
        f"{filename} starts with a terminal command. Run commands in a terminal or "
        "scripts/run_streamlit.ps1; do not paste them into Python source."
    )
    compile(source, str(path), "exec")
