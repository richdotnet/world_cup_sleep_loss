import sys
import os

# Ensure the project root is on the path so `app` and `src` can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server as app  # noqa: F401  — Vercel looks for `app`
