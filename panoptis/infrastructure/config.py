import logging
import os
import time
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class RouterConfig:
    """
    Loads router configuration from a YAML file.
    Supports hot-reloading by checking the file modification timestamp periodically.
    """

    def __init__(self, config_path: str, check_interval: float = 1.0):
        self.config_path = config_path
        self._config: dict[str, Any] = {}
        self._last_mtime: float = 0.0
        self._last_check: float = 0.0
        self._check_interval: float = check_interval

        # Initial load if file exists
        if os.path.exists(self.config_path):
            self._load()

    def _load(self) -> None:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            self._last_mtime = os.path.getmtime(self.config_path)
            logger.info("Loaded router config from %s", self.config_path)
        except Exception as e:
            logger.error("Failed to load router config from %s: %s", self.config_path, e)

    def get_config(self) -> dict[str, Any]:
        """
        Get the current configuration. Reloads from disk if the file was modified
        since the last check (throttled by check_interval).
        """
        now = time.monotonic()
        if now - self._last_check >= self._check_interval:
            self._last_check = now
            try:
                mtime = os.path.getmtime(self.config_path)
                if mtime > self._last_mtime:
                    self._load()
            except FileNotFoundError:
                logger.warning("Router config file %s not found.", self.config_path)
        return self._config
