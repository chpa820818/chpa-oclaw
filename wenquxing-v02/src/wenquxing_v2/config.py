from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    tenant_key: str
    owner_open_id: str
    data_dir: Path
    copilot_cli: Path
    copilot_timeout_seconds: int = 180
    worker_count: int = 4
    log_level: str = "INFO"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "wenquxing-v02.sqlite3"

    @property
    def copilot_work_dir(self) -> Path:
        return self.data_dir / "copilot-work"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "wenquxing-v02.log"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "wenquxing-v02.lock"


def load_settings() -> Settings:
    config_path = os.environ.get("WX_V2_CONFIG")
    if config_path:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"配置文件不存在：{path}")
        load_dotenv(path, override=False)

    required = {
        "WX_APP_ID": os.environ.get("WX_APP_ID", "").strip(),
        "WX_APP_SECRET": os.environ.get("WX_APP_SECRET", "").strip(),
        "WX_TENANT_KEY": os.environ.get("WX_TENANT_KEY", "").strip(),
        "WX_OWNER_OPEN_ID": os.environ.get("WX_OWNER_OPEN_ID", "").strip(),
        "WX_V2_DATA_DIR": os.environ.get("WX_V2_DATA_DIR", "").strip(),
        "WX_COPILOT_CLI": os.environ.get("WX_COPILOT_CLI", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ConfigurationError(f"缺少配置：{', '.join(missing)}")

    timeout = _positive_int("WX_COPILOT_TIMEOUT_SECONDS", 180)
    workers = _positive_int("WX_V2_WORKER_COUNT", 4)
    data_dir = Path(required["WX_V2_DATA_DIR"]).expanduser().resolve()
    copilot_cli = Path(required["WX_COPILOT_CLI"]).expanduser().resolve()
    if not copilot_cli.is_file():
        raise ConfigurationError(f"Copilot CLI 不存在：{copilot_cli}")

    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_id=required["WX_APP_ID"],
        app_secret=required["WX_APP_SECRET"],
        tenant_key=required["WX_TENANT_KEY"],
        owner_open_id=required["WX_OWNER_OPEN_ID"],
        data_dir=data_dir,
        copilot_cli=copilot_cli,
        copilot_timeout_seconds=timeout,
        worker_count=min(workers, 8),
        log_level=os.environ.get("WX_V2_LOG_LEVEL", "INFO").upper(),
    )


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value
