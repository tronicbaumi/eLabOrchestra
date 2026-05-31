import json
import os
from pathlib import Path
from typing import Any


class ConfigManager:
    """Load and save application configuration as JSON."""

    DEFAULT_DIR = Path.home() / ".elaborchestra"

    def __init__(self, config_dir: Path | None = None) -> None:
        self._dir = Path(config_dir) if config_dir else self.DEFAULT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}

    def load(self, name: str = "default") -> dict:
        path = self._dir / f"{name}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        return self._data

    def save(self, name: str = "default") -> None:
        path = self._dir / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def list_profiles(self) -> list[str]:
        return [p.stem for p in self._dir.glob("*.json")]

    @property
    def config_dir(self) -> Path:
        return self._dir
