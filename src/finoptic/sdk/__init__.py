"""FinOptic Python SDK — a thin, typed client over the FinOptic HTTP API.

Import the client directly::

    from finoptic.sdk import FinOpticClient

    with FinOpticClient("http://localhost:8000") as client:
        client.load_sample()
        print(client.summary()["total_monthly_waste"])
"""

from __future__ import annotations

from finoptic.sdk.client import FinOpticClient, __version__

__all__ = ["FinOpticClient", "__version__"]
