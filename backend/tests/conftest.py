"""Shared pytest setup.

Mirrors the fix in ../run.py: on Windows, the default Proactor event loop
breaks psycopg's async mode (raises OperationalError / getaddrinfo-style
failures on every async DB connection). run.py sets the Selector policy
before uvicorn is imported so the real server never hits this. Running
tests with plain `pytest` never goes through run.py, so without this same
fix here, every test that exercises the real async retrieval path (BM25/
dense search's connect_async) fails on Windows with a spurious connection
error that has nothing to do with the code under test.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
