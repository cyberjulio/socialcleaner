"""Entry point: python -m cli"""
import os
import sys

# Resolve project root (parent of cli/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

# Ensure project root is on sys.path so backend imports work
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
from cli.app import main

if __name__ == "__main__":
    asyncio.run(main())
