"""서비스 키가 URL 밖(예외·로그·JSONL·픽스처·상태 파일) 어디에도 남지 않는지 확인한다.

설정에 Decoding 값(원문)을 넣은 경우와 Encoding 값(% 포함)을 넣은 경우를 모두 본다.
"""

import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

from app.collector import cli
from app.collector.discover import discover
from app.collector.fixtures import save_fixture
from app.collector.logging_setup import quiet_http_loggers
from app.collector.redact import MASK, RedactingFilter, Redactor
from app.collector.status import StatusStore, status_path
from app.collector.storage import raw_poll_path, reference_dir
from app.collector.summary import summarize
from app.core.settings import (
    CALL_LIMITS,
    GBIS_BUS_LOCATION,
    REFERENCE_RECORD_SUFFIX,
    TARGETS_FILENAME,
)
from tests.collector.helpers import (
    FAKE_KEY,
    FAKE_KEY_ENCODED,
    FAKE_KEY_FORMS,
    TEST_TARGET,
    FakeClock,
    kst,
    load_fixture,
    make_client,
    make_collector,
    make_settings,
    read_all_text,
)

KEY_INPUTS = pytest.mark.parametrize(
    "key_input", [FAKE_KEY, FAKE_KEY_ENCODED], ids=["decoded", "encoded"]
)


def assert_no_key(text: str) -> None:
    for form in FAKE_KEY_FORMS:
        assert form not in text
        assert form.lower() not in text.lower()


@KEY_INPUTS
def test_settings_decodes_encoded_key_once(key_input: str) -> None:
    settings = make_settings(key_input)
    assert settings.service_key == FAKE_KEY
    assert FAKE_KEY not in repr(settings) and FAKE_KEY_ENCODED not in repr(settings)


@KEY_INPUTS
def test_exception_message_is_redacted(key_input: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"연결 실패 {request.url} raw={FAKE_KEY} enc={FAKE_KEY_ENCODED}", request=request
        )

    settings = make_settings(key_input)
    client = make_client(handler, settings, Redactor(settings.secret_values()))
    result = client.call(GBIS_BUS_LOCATION, {"routeId": "900000001"})

    assert result.ok is False and result.http_status is None
    assert result.error is not None and result.error.startswith("ConnectError")
    assert MASK in result.error
    assert_no_key(result.error)
    assert "serviceKey" not in result.params


@KEY_INPUTS
def test_jsonl_and_status_have_no_key(key_input: str, data_dir: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # 실제로는 키가 요청에 실려 나가야 한다(디코딩 값 한 번만 인코딩).
        assert request.url.params["serviceKey"] == FAKE_KEY
        calls["n"] += 1
        if calls["n"] == 1:
            # 응답 본문이 키를 그대로 되돌려 주는 최악의 경우
            echoed = load_fixture("gateway_error_synthetic.xml").replace(
                "</cmmMsgHeader>",
                f"<echo>{request.url}</echo><raw>{FAKE_KEY}</raw></cmmMsgHeader>",
            )
            return httpx.Response(200, text=echoed)
        if calls["n"] == 2:
            raise httpx.ReadTimeout(f"timeout {request.url}", request=request)
        return httpx.Response(200, text=load_fixture("bus_arrival_ok_synthetic.json"))

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, handler, key=key_input)
    outcomes = collector.poll_cycle()

    assert len(outcomes) == 3
    jsonl = raw_poll_path(data_dir, clock.now().date())
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        record = json.loads(line)
        assert "serviceKey" not in record["params"]
    assert json.loads(lines[0])["ok"] is False
    assert json.loads(lines[1])["error"].startswith("ReadTimeout")

    status_text = (data_dir / "status.json").read_text(encoding="utf-8")
    assert json.loads(status_text)["last_error"] is not None
    assert_no_key(read_all_text(data_dir))


@KEY_INPUTS
def test_fixture_file_has_no_key(key_input: str, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(load_fixture("bus_location_ok_synthetic.json"))
        body["response"]["msgHeader"]["echo"] = str(request.url)
        body["response"]["msgHeader"]["raw"] = FAKE_KEY
        return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))

    settings = make_settings(key_input)
    redactor = Redactor(settings.secret_values())
    result = make_client(handler, settings, redactor).call(GBIS_BUS_LOCATION, {"routeId": "1"})
    path = save_fixture(result, tmp_path / "fixtures", redactor, kst(2026, 10, 7, 6, 0))

    assert path.name.endswith("_real.json")
    text = path.read_text(encoding="utf-8")
    assert_no_key(text)
    assert json.loads(text)["response"]["msgHeader"]["raw"] == MASK


@KEY_INPUTS
def test_log_filter_redacts_message_args_and_exception(key_input: str) -> None:
    settings = make_settings(key_input)
    filt = RedactingFilter(Redactor(settings.secret_values()))
    try:
        raise RuntimeError(f"boom {FAKE_KEY_ENCODED}")
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "httpx",
        logging.WARNING,
        __file__,
        1,
        "url=%s raw=%s",
        (f"https://x/?serviceKey={FAKE_KEY_ENCODED}&a=1", FAKE_KEY),
        exc_info,
    )
    assert filt.filter(record) is True
    assert_no_key(record.getMessage())
    assert record.exc_text is not None
    assert_no_key(record.exc_text)


def test_service_key_query_param_is_masked_even_for_unknown_value() -> None:
    redactor = Redactor([])
    assert redactor.redact("a?serviceKey=SOMETHING&b=1") == f"a?serviceKey={MASK}&b=1"


def _echoing_handler(request: httpx.Request) -> httpx.Response:
    """모든 필드에 키를 되돌려 주는 응답.

    목록 이름이 달라도 첫 목록을 쓰는 해석을 이용해 모든 API 에 같은 본문을 준다.
    """
    item = {
        "routeId": "1",
        "routeName": "G1300",
        "regionName": FAKE_KEY,
        "startStationName": str(request.url),
        "endStationName": "잠실",  # discover 가 이 후보를 고르게 한다
        "stationId": "1",
        "stationName": f"덕현초교 {FAKE_KEY_ENCODED}",
        "stationSeq": 1,
        "turnYn": "N",
        "mobileNo": FAKE_KEY,
        "vehId": FAKE_KEY,
        "plateNo": FAKE_KEY_ENCODED,
        "stateCd": 1,
    }
    body = {
        "response": {
            "msgHeader": {"resultCode": 0, "resultMessage": f"ok {FAKE_KEY}"},
            "msgBody": {"items": [item]},
        }
    }
    return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))


@KEY_INPUTS
def test_discover_screen_reference_files_and_summary_have_no_key(
    key_input: str, data_dir: Path
) -> None:
    settings = make_settings(key_input)
    redactor = Redactor(settings.secret_values())
    client = make_client(_echoing_handler, settings, redactor)
    clock = FakeClock(kst(2026, 10, 7, 4, 30))
    status = StatusStore(
        status_path(data_dir), redactor, CALL_LIMITS, today=clock.now().date(), mode="discover"
    )

    lines: list[str] = []
    discover(client, status, data_dir, clock, lines.append, TEST_TARGET)
    assert any("덕현초교" in line for line in lines)
    assert_no_key("\n".join(lines))
    # 기준정보(원본 기록·targets.json)와 상태 파일에도 키가 없다.
    ref = reference_dir(data_dir, clock.now().date())
    assert (ref / TARGETS_FILENAME).exists()
    assert list(ref.glob(f"*{REFERENCE_RECORD_SUFFIX}"))
    assert_no_key(read_all_text(data_dir))

    result = client.call(GBIS_BUS_LOCATION, {"routeId": "1"})
    summary = "\n".join(summarize(result, "G1300", redactor))
    assert "vehId" in summary
    assert_no_key(summary)


def test_status_command_failure_prints_one_redacted_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(FAKE_KEY, collect_data_dir=str(tmp_path))
    redactor = Redactor(settings.secret_values())

    def broken_report(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError(f"broken serviceKey={FAKE_KEY_ENCODED} raw={FAKE_KEY}")

    monkeypatch.setattr(cli, "build_status_report", broken_report)
    assert cli.cmd_status(settings, redactor) == cli.EXIT_CALL_FAILED
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert_no_key(out)
    assert json.loads(out)["status_error"].startswith("RuntimeError")


def test_httpx_logger_is_quiet() -> None:
    quiet_http_loggers()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
