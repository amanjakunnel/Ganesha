"""Public ATS board discovery (Greenhouse, Lever, Ashby)."""

from __future__ import annotations

from packages.core.services.board_discovery.sync import (
    CompanyBoardSyncResult,
    sync_company_boards,
)

__all__ = [
    "CompanyBoardSyncResult",
    "sync_company_boards",
]
