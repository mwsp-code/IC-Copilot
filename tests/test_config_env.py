from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from equity_research import config


class ConfigEnvTests(unittest.TestCase):
    def test_parse_env_line_handles_quotes_and_comments(self) -> None:
        self.assertIsNone(config._parse_env_line("# comment"))
        self.assertEqual(config._parse_env_line('FRED_API_KEY="abc123"'), ("FRED_API_KEY", "abc123"))
        self.assertEqual(config._parse_env_line("ENABLE_DEFAULT_MACRO=true"), ("ENABLE_DEFAULT_MACRO", "true"))

    def test_local_env_loader_does_not_override_system_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            original_root = config.PROJECT_ROOT
            original_system_keys = set(config._SYSTEM_ENV_KEYS)
            original_value = os.environ.get("LOCAL_ENV_TEST_KEY")
            try:
                config.PROJECT_ROOT = Path(temporary)
                config._SYSTEM_ENV_KEYS = {"LOCAL_ENV_TEST_KEY"}
                os.environ["LOCAL_ENV_TEST_KEY"] = "system"
                (Path(temporary) / ".env.local").write_text(
                    'LOCAL_ENV_TEST_KEY="local"\nLOCAL_ENV_ONLY_KEY="loaded"\n',
                    encoding="utf-8",
                )
                config._load_local_env_files()
                self.assertEqual(os.environ["LOCAL_ENV_TEST_KEY"], "system")
                self.assertEqual(os.environ["LOCAL_ENV_ONLY_KEY"], "loaded")
            finally:
                config.PROJECT_ROOT = original_root
                config._SYSTEM_ENV_KEYS = original_system_keys
                if original_value is None:
                    os.environ.pop("LOCAL_ENV_TEST_KEY", None)
                else:
                    os.environ["LOCAL_ENV_TEST_KEY"] = original_value
                os.environ.pop("LOCAL_ENV_ONLY_KEY", None)


if __name__ == "__main__":
    unittest.main()
