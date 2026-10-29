"""手动备份命令：真实入队，模拟轮询时间及 worker 的完成记录。"""
import pytest
from sqlalchemy import select

from app import cli
from app.backup import job as backup_job
from app.db.base import utcnow
from app.db.models import BackupRecord, Job
from app.db import session


@pytest.fixture
def sm(db_sessionmaker, monkeypatch):
    monkeypatch.setattr(session, "get_sessionmaker", lambda: db_sessionmaker)
    return db_sessionmaker


def test_enqueue_and_busy(sm, capsys):
    assert cli.main(["backup-now"]) == 0
    with sm() as db:
        job = db.scalar(select(Job).where(Job.type == "backup"))
        assert job.payload == {"trigger": "manual", "user_id": None}
    assert cli.main(["backup-now"]) == 2
    assert "已有备份正在进行" in capsys.readouterr().out


@pytest.mark.parametrize("status,expected", [("success", 0), ("failed", 1)])
def test_wait_ignores_old_record_and_waits_for_worker(sm, monkeypatch, capsys, status, expected):
    import time

    with sm() as db:
        db.add(BackupRecord(status="success", trigger="manual", started_at=utcnow(),
                            finished_at=utcnow(), file_name="old.bak", size_bytes=1))
        db.commit()
    sleeps = []

    def finish(seconds):
        sleeps.append(seconds)
        with sm() as db:
            db.add(BackupRecord(status=status, trigger="manual", started_at=utcnow(),
                                finished_at=utcnow(), file_name="aireview_20261023_1200.bak",
                                size_bytes=123, error_message="磁盘空间不足" if status == "failed" else None))
            db.commit()

    monkeypatch.setattr(time, "sleep", finish)
    assert cli.main(["backup-now", "--wait"]) == expected
    out = capsys.readouterr()
    assert sleeps == [3]
    assert "old.bak" not in out.out
    assert ("123" in out.out) if status == "success" else ("磁盘空间不足" in out.err)


