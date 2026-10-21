"""Generate client configuration with this installation's actual absolute paths."""

from __future__ import annotations

import argparse
import json
import sys

from src.config import ROOT, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=str, help="Optional JSON file; otherwise print to stdout")
    args = parser.parse_args()
    config = {
        "mcpServers": {
            "odoo19-docs": {
                "command": sys.executable,
                "args": [str(ROOT / "src/server.py")],
                "cwd": str(ROOT),
                "env": {
                    "ODOO_RAG_DATA_DIR": str(Settings.from_env().data_dir),
                    "ODOO_RAG_EMBEDDING_PROVIDER": "local",
                    "ODOO_RAG_OFFLINE": "true",
                },
            }
        }
    }
    rendered = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
