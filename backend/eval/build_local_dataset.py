"""Build the independent public due-diligence snapshot.

Examples from the repository root::

    python backend/eval/build_local_dataset.py
    python backend/eval/build_local_dataset.py --download-repositories --download-findoc-reports
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .download_findoc_reports import download as download_reports
    from .download_public_datasets import download as download_repositories
    from .normalize_public_datasets import normalize
except ImportError:
    from download_findoc_reports import download as download_reports
    from download_public_datasets import download as download_repositories
    from normalize_public_datasets import normalize


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root / "data" / "public_dd")
    parser.add_argument("--download-repositories", action="store_true")
    parser.add_argument("--download-findoc-reports", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    result: dict[str, object] = {"root": args.root.resolve().as_posix()}
    if args.download_repositories:
        result["repository_acquisition"] = download_repositories(args.root)
    if args.download_findoc_reports:
        reports = download_reports(args.root, workers=args.workers)
        result["report_acquisition"] = {
            key: reports[key] for key in ("requested", "downloaded", "existing", "failed", "total_bytes")
        }
    result["snapshot"] = normalize(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    report = result.get("report_acquisition") or {}
    return 1 if isinstance(report, dict) and report.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
