"""Allow `python3 -m raddu`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())