#!/usr/bin/env python3
"""Project-scoped Stop hook; configuration is opt-in, never installed globally."""
from pathlib import Path
import json
import sys

# Resolve this checked-in adapter's package, not arbitrary imports in the CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writing_memory.integrations import run_stop_hook


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        result = run_stop_hook(payload)
        print(json.dumps({"continue": True, "systemMessage": "Writing Memory 本地记录：" + json.dumps(result, ensure_ascii=False)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Import errors are persisted by SourceImporter. Make failures visible to
        # Codex, while avoiding transcript text and credentials in hook stderr.
        print("writing-memory Stop hook failed: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
