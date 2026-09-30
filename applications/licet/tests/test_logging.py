import json

import pytest

from licet.logging.logger import RunLogger, StepLog, new_run_id


def test_run_logger_writes_structured_event(tmp_path):
    logger = RunLogger("run-1", tmp_path)
    logger.log(StepLog(user_request="Find permit X", step_count=1, observation="Results"))

    records = logger.read_all()
    assert len(records) == 1
    assert records[0]["run_id"] == "run-1"
    assert records[0]["event"] == "step"
    assert records[0]["timestamp"]
    assert records[0]["user_request"] == "Find permit X"
    json.dumps(records[0])


def test_new_run_ids_are_unique_and_filename_safe():
    first = new_run_id("phase 1/browser")
    second = new_run_id("phase 1/browser")
    assert first != second
    assert "/" not in first and "\\\\" not in first


def test_logger_tracks_primitive_action_metrics(tmp_path):
    logger = RunLogger("run-metrics", tmp_path)
    logger.log(StepLog(user_request="g", browser_action="click Search", browser_result={"success": True}))
    logger.log(StepLog(user_request="g", browser_action="click Search", browser_result={"success": False}))
    assert logger.summary()["click"] == {"attempts": 2, "success": 1, "failure": 1}


def test_logger_records_named_aggregate_metrics(tmp_path):
    logger = RunLogger("run-kpi", tmp_path)
    logger.log_metrics("lookup", {"attempts": 3, "wrong_record_rate": 0.0})
    assert logger.aggregates["lookup"]["attempts"] == 3
    events = [record for record in logger.read_all() if record["event"] == "metrics"]
    assert events and events[0]["name"] == "lookup"
    assert events[0]["values"]["wrong_record_rate"] == 0.0


def test_run_id_cannot_escape_log_directory(tmp_path):
    with pytest.raises(ValueError):
        RunLogger("../outside", tmp_path)
