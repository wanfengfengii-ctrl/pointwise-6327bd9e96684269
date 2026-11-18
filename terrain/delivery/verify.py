"""Single verification entry: original regressions plus the change-band chain.

Run ``python3 -B -m terrain.delivery.verify``.  It executes the full unittest
suite first (which already covers the original packages, contours and bands)
and then the independent change-band reconciliation.  Any failure propagates a
non-zero exit code.
"""
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run(argv):
    print('RUN ' + ' '.join(argv), flush=True)
    return subprocess.run(argv, cwd=ROOT, check=False).returncode


def main(argv=None):
    steps = (
        [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-v'],
        [sys.executable, '-B', '-m', 'terrain.archive.api', '--sample', 'samples/day_shift.json'],
        [sys.executable, '-B', '-m', 'terrain.delivery.reconcile', '--divisions', '8'],
        [sys.executable, '-B', 'change_band_cases.py', '--exercise'],
    )
    failures = [argv for argv in steps if _run(argv) != 0]
    if failures:
        print('FAILED ' + ' | '.join(' '.join(argv) for argv in failures), file=sys.stderr)
        return 1
    print('VERIFY OK: original regressions and change-band chain passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
