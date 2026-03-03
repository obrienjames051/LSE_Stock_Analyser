"""
utils.py
--------
Shared utilities used across all modules:
  - console : single Rich Console instance
  - silent(): context manager to suppress yfinance stderr noise
"""

import os
import sys
import contextlib
from rich.console import Console

console = Console()


@contextlib.contextmanager
def silent():
    """Redirect stderr to /dev/null to suppress yfinance error spam."""
    with open(os.devnull, "w") as devnull:
        old, sys.stderr = sys.stderr, devnull
        try:
            yield
        finally:
            sys.stderr = old
