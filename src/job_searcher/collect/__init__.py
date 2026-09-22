# Importing the adapter modules is what registers them.
from job_searcher.collect.ats import greenhouse  # noqa: F401
from job_searcher.collect.base import (
    AdapterError,
    RawListing,
    SourceAdapter,
    get_adapter,
    registered_kinds,
)

__all__ = [
    "AdapterError",
    "RawListing",
    "SourceAdapter",
    "get_adapter",
    "registered_kinds",
]
