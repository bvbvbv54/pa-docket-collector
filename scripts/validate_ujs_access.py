"""Shared-network, read-only UJS reachability entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_ujs_page import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
