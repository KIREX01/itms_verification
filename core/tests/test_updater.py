"""
Test discovery wrapper for Update Service & GitHub Releases tests.
"""
from core.tests_updater import (
    VersionTests,
    UpdateCacheTests,
    UpdateServiceCheckTests,
    UpdateServiceApplyTests,
    UpdateEndpointsAndCommandTests,
)

__all__ = [
    "VersionTests",
    "UpdateCacheTests",
    "UpdateServiceCheckTests",
    "UpdateServiceApplyTests",
    "UpdateEndpointsAndCommandTests",
]
