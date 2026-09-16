from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from .app import run
from .config import ConfigurationError, load_settings
from .copilot import CopilotRunner
from .feishu import FeishuGateway
from .service import ConversationService
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="wenquxing-v02")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("doctor")
    subparsers.add_parser("send-ready")
    args = parser.parse_args()

    try:
        settings = load_settings()
        store = Store(settings.database_path)
        store.initialize()
        if args.command == "run":
            run(settings)
        elif args.command == "doctor":
            copilot = CopilotRunner(
                settings.copilot_cli,
                settings.copilot_work_dir,
                settings.copilot_timeout_seconds,
            )
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "database": str(settings.database_path),
                        "copilot": copilot.version(),
                        "jobs": store.status_counts(),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "send-ready":
            copilot = CopilotRunner(
                settings.copilot_cli,
                settings.copilot_work_dir,
                settings.copilot_timeout_seconds,
            )
            service = ConversationService(store, copilot)
            gateway = FeishuGateway(settings, service)
            stamp = datetime.now(UTC).strftime("%Y%m%d%H")
            gateway.send_text(
                settings.owner_open_id,
                "文曲星 v02 已部署并上线。发送“会话”可创建或切换会话。",
                f"wenquxing-v02-ready:{stamp}",
            )
            print("ready message sent")
    except (ConfigurationError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
