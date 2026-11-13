"""Background / CLI scoring of unscored role-matched jobs with progress status."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services.role_profile import assess_fit_batch, feed_counts_snapshot

DEFAULT_BATCH_SIZE = 200
DEFAULT_MAX_ROUNDS = 100
REFRESH_SCORE_LIMIT = 500


@dataclass
class ScoreJobState:
    score_id: str
    status: str  # idle | running | completed | failed
    started_at: str | None = None
    finished_at: str | None = None
    progress: str = ""
    assessed: int = 0
    rounds: int = 0
    bands: dict[str, int] = field(default_factory=dict)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_id": self.score_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "assessed": self.assessed,
            "rounds": self.rounds,
            "bands": dict(self.bands),
            "message": self.message,
        }


_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_ACTIVE: ScoreJobState | None = None
_HISTORY: list[ScoreJobState] = []


def get_score_status() -> dict[str, Any]:
    with _STATE_LOCK:
        active = _ACTIVE
        last = _HISTORY[-1] if _HISTORY else None
    return {
        "active": active.to_dict() if active else None,
        "last_completed": last.to_dict() if last else None,
    }


def start_score_remaining(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> tuple[ScoreJobState, int]:
    """Start background score-remaining. 202 started, 409 already running."""
    global _ACTIVE
    with _LOCK:
        with _STATE_LOCK:
            if _ACTIVE is not None and _ACTIVE.status == "running":
                return _ACTIVE, 409
        state = ScoreJobState(
            score_id=str(uuid.uuid4()),
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            progress="Scoring jobs…",
        )
        with _STATE_LOCK:
            _ACTIVE = state
        thread = threading.Thread(
            target=_run_score_remaining,
            args=(state.score_id, batch_size, max_rounds),
            name=f"ganesha-score-{state.score_id[:8]}",
            daemon=True,
        )
        thread.start()
        return state, 202


def score_remaining(
    session: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Loop assess_fit_batch until no more role-matched unscored jobs (or max rounds)."""
    total_assessed = 0
    bands: dict[str, int] = {}
    rounds = 0
    for round_i in range(1, max_rounds + 1):
        rounds = round_i
        if progress_callback:
            progress_callback(
                f"Scoring jobs… round {round_i} (batch {batch_size}, "
                f"assessed so far {total_assessed})"
            )
        result = assess_fit_batch(
            session, limit=batch_size, role_filtered_only=True
        )
        session.commit()
        n = int(result.get("assessed") or 0)
        total_assessed += n
        for band, count in (result.get("bands") or {}).items():
            bands[band] = bands.get(band, 0) + int(count)
        if progress_callback:
            progress_callback(
                f"Scoring jobs… round {round_i} done "
                f"(+{n}, total {total_assessed})"
            )
        if n == 0:
            break
    snap = feed_counts_snapshot(session)
    return {
        "assessed": total_assessed,
        "rounds": rounds,
        "bands": bands,
        "feed_counts": (snap.get("counts") if isinstance(snap, dict) else snap),
        "stopped_reason": "complete" if rounds < max_rounds or total_assessed == 0 else "max_rounds",
    }


def _run_score_remaining(score_id: str, batch_size: int, max_rounds: int) -> None:
    global _ACTIVE, _HISTORY
    session = SessionLocal()
    try:
        with _STATE_LOCK:
            state = _ACTIVE
        if state is None or state.score_id != score_id:
            return

        def _progress(msg: str) -> None:
            with _STATE_LOCK:
                state.progress = msg

        result = score_remaining(
            session,
            batch_size=batch_size,
            max_rounds=max_rounds,
            progress_callback=_progress,
        )
        state.assessed = int(result.get("assessed") or 0)
        state.rounds = int(result.get("rounds") or 0)
        state.bands = dict(result.get("bands") or {})
        state.status = "completed"
        state.message = (
            f"Scored {state.assessed} job(s) in {state.rounds} round(s)."
        )
        state.progress = "Done"
        state.finished_at = datetime.now(UTC).isoformat()
        with _STATE_LOCK:
            _HISTORY.append(state)
            if len(_HISTORY) > 20:
                del _HISTORY[:-20]
            _ACTIVE = None
    except Exception as exc:
        session.rollback()
        with _STATE_LOCK:
            if _ACTIVE and _ACTIVE.score_id == score_id:
                _ACTIVE.status = "failed"
                _ACTIVE.message = f"{exc.__class__.__name__}: {exc}"[:240]
                _ACTIVE.finished_at = datetime.now(UTC).isoformat()
                _ACTIVE.progress = "Failed"
                _HISTORY.append(_ACTIVE)
                _ACTIVE = None
    finally:
        session.close()


def reset_score_state_for_tests() -> None:
    global _ACTIVE, _HISTORY
    with _LOCK:
        with _STATE_LOCK:
            _ACTIVE = None
            _HISTORY.clear()
