"""NGSAT configuration service — DB-backed key-value store with in-memory cache.

ConfigService provides runtime configuration persistence,
replacing direct .env file manipulation.

Data priority:
  1. .env file (initial values on startup)
  2. DB (runtime changes via dashboard)
  3. Code defaults (fallback)

Usage:
  config_service = ConfigService(db_session)
  val = config_service.get("buy_threshold", default=0.65)
  config_service.set("buy_threshold", 0.70)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from core.logger import logger
from core.models import SystemConfig


class ConfigService:
    """DB-backed configuration service with in-memory cache.

    Stores StrategyConfig overrides in the system_config table.
    The cache avoids DB reads on every get() call.
    """

    def __init__(self, session):
        self._session = session
        self._cache: dict[str, str] = {}

    # ── Public API ──

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key.

        Checks cache first, then DB. Returns default if not found.
        """
        if key in self._cache:
            return self._parse_value(self._cache[key])

        record = self._session.query(SystemConfig).filter(
            SystemConfig.key == key
        ).first()
        if record:
            self._cache[key] = record.value
            return self._parse_value(record.value)

        return default

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        """Set a config value.

        Updates cache immediately. Persists to DB when persist=True (default).
        """
        str_value = str(value)
        self._cache[key] = str_value

        if persist:
            record = self._session.query(SystemConfig).filter(
                SystemConfig.key == key
            ).first()
            if record:
                record.value = str_value
                record.updated_at = datetime.now()
            else:
                self._session.add(SystemConfig(
                    key=key, value=str_value,
                ))
            self._session.commit()
            logger.debug(f"ConfigService: {key} = {str_value} 저장됨")

    def delete(self, key: str) -> None:
        """Remove a config value (revert to .env / default)."""
        self._cache.pop(key, None)
        self._session.query(SystemConfig).filter(
            SystemConfig.key == key
        ).delete()
        self._session.commit()
        logger.info(f"ConfigService: {key} 삭제됨 — 기본값으로 복원")

    def load_all(self) -> dict[str, str]:
        """Load all config entries into cache. Returns dict of key→value."""
        records = self._session.query(SystemConfig).all()
        self._cache = {r.key: r.value for r in records}
        return dict(self._cache)

    def apply_to(self, config_obj: Any, field_map: dict[str, str]) -> int:
        """Apply DB-stored overrides to a config object.

        Args:
            config_obj: Target object (e.g. StrategyConfig instance).
            field_map: Mapping of DB keys → config attribute names.

        Returns:
            Number of overrides applied.
        """
        applied = 0
        for db_key, attr_name in field_map.items():
            db_value = self.get(db_key)
            if db_value is not None and hasattr(config_obj, attr_name):
                current = getattr(config_obj, attr_name)
                if type(current) == float:
                    setattr(config_obj, attr_name, float(db_value))
                elif type(current) == int:
                    setattr(config_obj, attr_name, int(float(db_value)))
                elif type(current) == bool:
                    setattr(config_obj, attr_name, db_value.lower() == "true")
                else:
                    setattr(config_obj, attr_name, db_value)
                applied += 1
        return applied

    # ── Internal ──

    @staticmethod
    def _parse_value(value: str) -> Any:
        """Try to parse string to number. Returns string if not numeric."""
        try:
            if "." in value:
                return float(value)
            return int(value)
        except (ValueError, TypeError):
            return value
