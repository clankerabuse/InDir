import sys

from indir.cli import main

if __name__ == "__main__":
    # `python -m indir` must report the run's status, the same as the console
    # script: a request that could not be verified exits non-zero.
    sys.exit(main())
