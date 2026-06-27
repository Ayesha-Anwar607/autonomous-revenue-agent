"""
Shared pytest configuration.
Sets asyncio loop scope to 'session' so module-scoped async fixtures
work correctly with pytest-asyncio 1.x.
"""
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "memory: Phase 3 memory layer tests")
