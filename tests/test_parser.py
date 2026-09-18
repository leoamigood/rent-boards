import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batumi_rent import parser
from samples import SAMPLES


def main() -> int:
    failures = 0
    for i, (text, expected) in enumerate(SAMPLES, 1):
        got = parser.parse(text)
        bad = {k: (v, got.get(k)) for k, v in expected.items() if got.get(k) != v}
        status = "ok " if not bad else "FAIL"
        print(f"[{status}] #{i} {text.splitlines()[0][:58]}")
        for key, (want, actual) in bad.items():
            print(f"        {key}: expected {want!r}, got {actual!r}")
            failures += 1
    print(f"\n{failures} mismatched field(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
