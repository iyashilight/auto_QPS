from qps.infra.storage.research_db import ResearchStore


def test_all_v2_research_tables_reject_update_delete(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")
    with store.connect() as conn:
        trigger_names = ("research_candidate_families_no_update", "research_reports_no_update", "research_feature_evaluations_no_update", "research_blind_results_no_update", "research_selections_no_update")
        for name in trigger_names:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name = ?", (name,)).fetchone() is not None
