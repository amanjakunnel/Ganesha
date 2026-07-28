from packages.core.domain.models import Base
from packages.core.db import SessionLocal, get_engine
from packages.core.services import workflow_service


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_demo_loop_creates_fit_application_and_decision() -> None:
    session = SessionLocal()
    try:
        result = workflow_service.run_demo_loop(session)
        session.commit()
        assert result["job_id"]
        assert result["application_id"]
        assert result["fit_id"]
        assert result["decision_id"]
        assert result["lifecycle"] in ("ready_to_apply", "needs_decision", "draft_ready")
        summary = workflow_service.dashboard_summary(session)
        assert summary["jobs_total"] >= 1
        assert summary["applications_total"] >= 1
    finally:
        session.close()
