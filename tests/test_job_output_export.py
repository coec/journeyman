import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.job_output_export import build_job_output_export


def _step(position, name, stdout="", stderr="", status="successful"):
    return SimpleNamespace(
        position=position,
        name=name,
        stdout=stdout,
        stderr=stderr,
        status=status,
    )


def _job(job_id, steps):
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=job_id,
        project_name="Output export project",
        status="successful",
        requested_by="admin",
        queued_at=now,
        started_at=now,
        finished_at=now,
        exit_code=0,
        steps=steps,
    )


def test_single_step_stdout_only_is_plain_text():
    export = build_job_output_export(_job(169, [_step(1, "Question", "hello\n")]))
    assert export.filename == "journeyman-job-169-stdout.txt"
    assert export.mimetype.startswith("text/plain")
    assert export.data == b"hello\n"


def test_single_step_stderr_only_is_plain_text():
    export = build_job_output_export(_job(169, [_step(1, "Question", stderr="oops\n")]))
    assert export.filename == "journeyman-job-169-stderr.txt"
    assert export.data == b"oops\n"


def test_single_step_stdout_and_stderr_is_zip():
    export = build_job_output_export(
        _job(169, [_step(1, "Question", stdout="out\n", stderr="err\n")])
    )
    with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
        names = set(archive.namelist())
        assert "job-169.txt" in names
        assert "stdout.txt" in names
        assert "stderr.txt" in names
        assert archive.read("stdout.txt") == b"out\n"
        assert archive.read("stderr.txt") == b"err\n"


def test_multi_step_export_uses_step_directories_and_omits_empty_streams():
    export = build_job_output_export(
        _job(
            169,
            [
                _step(1, "Question", stdout="one\n"),
                _step(2, "Answer / Verify", stdout="two\n", stderr="oops\n"),
            ],
        )
    )
    with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
        names = set(archive.namelist())
        assert "job-169.txt" in names
        assert "step-01-question/stdout.txt" in names
        assert "step-01-question/stderr.txt" not in names
        assert "step-02-answer-verify/stdout.txt" in names
        assert "step-02-answer-verify/stderr.txt" in names


def test_single_step_without_output_returns_job_summary_text():
    export = build_job_output_export(_job(169, [_step(1, "Question")]))
    assert export.filename == "journeyman-job-169-output.txt"
    assert b"Journeyman Job #169" in export.data
    assert b"01. Question" in export.data
