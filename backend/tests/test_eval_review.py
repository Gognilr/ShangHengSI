"""M2-18：真实解析、脱敏、审核持久化与网关，网络仅用 MockTransport。"""
import json

import pytest
from sqlalchemy import func, select

from app.admin.settings_store import get_setting, set_setting
from app.config import get_settings
from app.db.models import Job, LlmCall, Task
from tools import eval_review
from tests.review_support import env, llm, sample  # noqa: F401


@pytest.fixture
def cli(tmp_path, monkeypatch, db_engine, pg_url, env, make_user):
    make_user("evaluator")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", pg_url)
    get_settings.cache_clear()
    monkeypatch.setattr(eval_review, "get_engine", lambda: db_engine)
    directory = tmp_path / "samples"
    directory.mkdir()
    (directory / "contract.docx").write_bytes(sample("contract_basic"))
    report = tmp_path / "report.md"
    return directory, report, [str(directory), "--output", str(report), "--username", "evaluator"]


def test_cli_full_pipeline_and_temporary_parameters(cli, llm, db_sessionmaker):
    directory, report, args = cli
    (directory / "ignored.yaml").write_text("ignored")
    requests = llm()
    with db_sessionmaker() as db:
        set_setting(db, "model.extra_params", {"seed": 7, "thinking": {"type": "enabled"}}, None)
        db.commit()
    assert eval_review.main(args + ["--extra-params", '{"thinking":{"type":"disabled"}}']) == 0
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["seed"] == 7
    assert body["thinking"] == {"type": "disabled"}
    assert "星河虚构" not in json.dumps(body, ensure_ascii=False)
    text = report.read_text(encoding="utf-8")
    assert "要求结论统计" in text and "各要求结论" in text and "问题清单" in text
    assert "C\\-01" in text and "prompt_version" in text and "2026" in text
    with db_sessionmaker() as db:
        assert get_setting(db, "model.extra_params") == {"seed": 7, "thinking": {"type": "enabled"}}
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 1
        assert set(db.scalars(select(Job.status))) == {"done"}
        assert db.scalar(select(Task.status)) == "reviewed"


@pytest.mark.parametrize("mode,expected", [("missing_requirement", "partial"), ("non_json", "failed")])
def test_model_output_validation(cli, llm, db_sessionmaker, mode, expected):
    _, report, args = cli
    llm(mode)
    assert eval_review.main(args + ["--extra-params", '{"thinking":{"type":"enabled"}}']) == (0 if expected == "partial" else 1)
    assert expected in report.read_text(encoding="utf-8")
    with db_sessionmaker() as db:
        assert get_setting(db, "model.extra_params") == {}


@pytest.mark.parametrize("cap", [True, False])
def test_gateway_cap_and_recheck(cli, llm, db_sessionmaker, cap):
    _, report, args = cli
    requests = llm()
    with db_sessionmaker() as db:
        if cap:
            set_setting(db, "model.monthly_call_cap", 1, None)
            db.add(LlmCall(purpose="test", model_name="mock-model", status="sent"))
        else:
            set_setting(db, "review.company_profile", "联系 forbidden@example.test", None)
        db.commit()
    assert eval_review.main(args) == 1
    assert not requests
    text = report.read_text(encoding="utf-8")
    assert ("REVIEW\\_FAILED" if cap else "OUTBOUND\\_BLOCKED") in text
    assert "forbidden@example" not in text


def test_bad_sample_does_not_stop_later_samples(cli, llm):
    directory, report, args = cli
    (directory / "00_bad.docx").write_bytes(b"not a docx")
    requests = llm()
    assert eval_review.main(args) == 1
    assert len(requests) == 1
    assert "样本总数：2" in report.read_text(encoding="utf-8")


def test_no_our_party_does_not_call_model(cli, llm):
    directory, report, args = cli
    (directory / "contract.docx").write_bytes(sample("numbering_auto"))
    requests = llm()
    assert eval_review.main(args) == 1
    assert not requests
    assert "OUR\\_PARTY\\_REQUIRED" in report.read_text(encoding="utf-8")


def test_empty_directory_and_disabled_user(cli, llm, db_sessionmaker):
    from app.db.models import User

    directory, report, args = cli
    requests = llm()
    with db_sessionmaker() as db:
        user = db.scalars(select(User).where(User.username == "evaluator")).one()
        user.status = "disabled"
        db.commit()
    assert eval_review.main(args) == 2
    assert not requests
    assert not report.exists()
    (directory / "contract.docx").unlink()
    assert eval_review.main(args) == 2
    assert not report.exists()


@pytest.mark.parametrize("app_env,database", [(None, "aireview_eval_dev"), ("production", "aireview_eval_dev"),
                                              ("development", "aireview")])
def test_refuse_non_development_before_database_or_model(monkeypatch, tmp_path, app_env, database):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://localhost/{database}")
    get_settings.cache_clear()
    monkeypatch.setattr(eval_review, "get_engine", lambda: pytest.fail("不应连接数据库"))
    output = tmp_path / "report.md"
    assert eval_review.main([str(tmp_path), "--output", str(output), "--username", "evaluator"]) == 2
    assert not output.exists()
    get_settings.cache_clear()


def test_report_refuses_overwrite_before_call(cli, llm):
    _, report, args = cli
    report.write_text("existing", encoding="utf-8")
    requests = llm()
    assert eval_review.main(args) == 2
    assert not requests and report.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize("raw", ["[]", "null", "bad"])
def test_extra_params_must_be_object(raw):
    with pytest.raises(Exception, match="JSON 对象"):
        eval_review.json_object(raw)


def test_report_escapes_untrusted_markdown():
    text = eval_review.render_report([{"sample": "<script>|[x](https://x)\n# title", "sha256": "abc",
                                      "status": "failed", "error": "ERROR", "detail": None}],
                                    stance="sales", source="outgoing", extra_params={"secret": "hidden"})
    assert "<script>" not in text and "[x](https://x)" not in text
    assert "&lt;script&gt;\\|" in text and "<br>\\#" in text
    assert "hidden" not in text
