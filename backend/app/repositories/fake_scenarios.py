"""개발·시연용 가짜 스냅샷(계약 12장).

가짜라도 규칙과 모순되지 않도록, 버스·사례 수 같은 '입력'만 손으로 정한다.
선행시간 선택·위험 등급·대안·서비스 상태·다음 조회 시각은
app/forecast 의 실제 함수로 계산한다.
scenario 없음/example 은 계약 9장 예시와 값이 같다(테스트가 문서의 JSON 과 비교한다).

TODO(main/2026-10-06): DB 저장소 도입 시 규칙 계산을 services 로 올린다.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.core.settings import (
    COLLECT_TARGET,
    FORECAST_RULES,
    KST,
    ROUTE_1306,
    ROUTE_G1300,
    RULES_VERSION,
    SERVICE_OUTSIDE_MESSAGE,
    TargetRoute,
)
from app.forecast.alternatives import decide_alternatives, meets_deadline, resolve_deadline
from app.forecast.lead_time import select_lead_time
from app.forecast.risk import no_seat_probability
from app.forecast.service_time import in_forecast_hours, is_stale, next_refresh_at, service_state
from app.schemas.common import ArrivalEstimateSource, BusSource, ForecastStatus, RiskLevel
from app.schemas.snapshot import (
    AlternativeCandidate,
    Alternatives,
    Bus,
    Forecast,
    ForecastInputs,
    Recommended,
    ServiceInfo,
    SnapshotDestination,
    SnapshotResponse,
    SnapshotStation,
)

# ---------------------------------------------------------------------------
# 기준정보(가짜). DB 기준정보(station·route·route_station)가 채워지면 저장소에서 읽는다.
# 노선 ID·이름은 settings, 하차 정류장은 settings 주석의 discover 결과(2026-10-06).
# ---------------------------------------------------------------------------
BOARD_STATION_ID = COLLECT_TARGET.board_station_id or ""
BOARD_STATION_NAME = "덕현초교.덕고개"
BOARD_STATION_MOBILE_NO = "39624"
DESTINATION_ID = "jamsil"
DESTINATION_NAME = COLLECT_TARGET.destination_name


@dataclass(frozen=True)
class AlightStop:
    route: TargetRoute
    station_id: str
    station_name: str


ALIGHT_STOPS: tuple[AlightStop, ...] = (
    AlightStop(ROUTE_G1300, "123000611", "잠실광역환승센터"),
    AlightStop(ROUTE_1306, "123000002", "잠실역.잠실대교남단(중)"),
)

# 9장 예시의 snapshotId. 나머지 scenario 는 이름에서 만든 고정 uuid5 를 쓴다.
EXAMPLE_SNAPSHOT_ID = uuid.UUID("0d6f3c1e-8a52-4f0b-9c2a-5d1f0e7b2a11")
_SNAPSHOT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "yangju-bus/fake-snapshot")

G1300_VEHICLE = ("235000359", "경기76바8260")
BUS_1306_VEHICLE = ("235010110", "경기76바8309")

FAKE_DAY = date(2026, 10, 7)  # 수요일


def _at(hour: int, minute: int, second: int = 0, day: date = FAKE_DAY) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=KST)


def _route_id(route: TargetRoute) -> str:
    if route.route_id is None:
        raise ValueError(f"routeId 가 없는 노선: {route.route_name}")
    return route.route_id


# ---------------------------------------------------------------------------
# scenario 입력
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IssuedSpec:
    """시점이 지난 선행시간 예보의 결과(입력)."""

    status: ForecastStatus
    n: int | None = None
    k: int | None = None
    seats: int | None = None
    headway_min: float | None = None
    preliminary: bool | None = None  # None 이면 scenario 기본값


@dataclass(frozen=True)
class BusSpec:
    route: TargetRoute
    vehicle: tuple[str, str] | None  # (vehicleId, plateNo)
    source: BusSource
    station_arrival_at: datetime | None
    current_seats: int | None
    seats_updated_at: datetime | None
    destination_arrival_at: datetime | None
    issued: Mapping[int, IssuedSpec] = field(default_factory=dict)
    arrival_estimate_source: ArrivalEstimateSource | None = "predict_time_sec"


@dataclass(frozen=True)
class ScenarioSpec:
    computed_at: datetime
    data_updated_at: datetime | None
    deadline: str | None
    buses: tuple[BusSpec, ...]
    preliminary: bool = True


# ---------------------------------------------------------------------------
# 계산(실제 규칙 함수 사용)
# ---------------------------------------------------------------------------
def _unissued_forecast(
    lead: int, status: ForecastStatus, issued_at: datetime | None, preliminary: bool
) -> Forecast:
    return Forecast(
        lead_time_min=lead,
        status=status,
        issued_at=issued_at,
        no_seat_probability=None,
        n=None,
        k=None,
        risk_level=None,
        preliminary=preliminary,
        inputs=ForecastInputs(seats=None, headway_min=None),
    )


def _issued_probability(lead: int, spec: IssuedSpec) -> tuple[float | None, RiskLevel | None]:
    """ok 예보의 (noSeatProbability, riskLevel). 확률은 항상 k/n 에서 나온다."""
    min_cases = FORECAST_RULES.min_cases_to_show
    if spec.status == "ok":
        if spec.n is None or spec.k is None or spec.n < min_cases:
            raise ValueError(f"{lead}분 ok 예보는 n ≥ {min_cases} 와 k 가 있어야 한다")
        return no_seat_probability(spec.k, spec.n)
    if spec.status == "insufficient_cases" and (spec.n is None or spec.n >= min_cases):
        raise ValueError(f"{lead}분 사례 부족 예보는 n < {min_cases} 여야 한다")
    if spec.status in ("not_yet", "outside_hours"):
        raise ValueError(f"{spec.status} 는 시각으로 정해지므로 입력으로 주지 않는다")
    return None, None


def _build_forecast(lead: int, bus: BusSpec, scenario: ScenarioSpec) -> Forecast:
    now = scenario.computed_at
    arrival = bus.station_arrival_at
    issue_at = arrival - timedelta(minutes=lead) if arrival is not None else None
    if arrival is None or issue_at is None or issue_at > now:
        return _unissued_forecast(lead, "not_yet", None, scenario.preliminary)
    if not in_forecast_hours(arrival):
        return _unissued_forecast(lead, "outside_hours", issue_at, scenario.preliminary)
    spec = bus.issued.get(lead)
    if spec is None:
        raise ValueError(f"{bus.route.route_name} {lead}분 예보 입력이 없다")
    probability, risk = _issued_probability(lead, spec)
    return Forecast(
        lead_time_min=lead,
        status=spec.status,
        issued_at=issue_at,
        no_seat_probability=probability,
        n=spec.n,
        k=spec.k,
        risk_level=risk,
        preliminary=scenario.preliminary if spec.preliminary is None else spec.preliminary,
        inputs=ForecastInputs(seats=spec.seats, headway_min=spec.headway_min),
    )


def _build_bus(bus: BusSpec, scenario: ScenarioSpec) -> Bus:
    now = scenario.computed_at
    leads = sorted(FORECAST_RULES.lead_times_min, reverse=True)
    forecasts = [_build_forecast(lead, bus, scenario) for lead in leads]
    arrival = bus.station_arrival_at
    minutes = None
    if arrival is not None:
        minutes = max(0, int((arrival - now).total_seconds() // 60))
    vehicle_id, plate_no = bus.vehicle if bus.vehicle else (None, None)
    return Bus(
        route_id=_route_id(bus.route),
        route_name=bus.route.route_name,
        vehicle_id=vehicle_id,
        plate_no=plate_no,
        source=bus.source,
        station_arrival_at=arrival,
        arrival_estimate_source=bus.arrival_estimate_source,
        minutes_to_arrival=minutes,
        in_forecast_hours=in_forecast_hours(arrival) if arrival is not None else False,
        current_seats=bus.current_seats,
        seats_updated_at=bus.seats_updated_at,
        selected_lead_time_min=select_lead_time(forecasts, now),
        forecasts=forecasts,
    )


def _build_candidate(bus: Bus, spec: BusSpec, deadline_at: datetime | None) -> AlternativeCandidate:
    selected = next(
        (f for f in bus.forecasts if f.lead_time_min == bus.selected_lead_time_min), None
    )
    return AlternativeCandidate(
        route_id=bus.route_id,
        route_name=bus.route_name,
        vehicle_id=bus.vehicle_id,
        source=bus.source,
        station_arrival_at=bus.station_arrival_at,
        destination_arrival_at=spec.destination_arrival_at,
        meets_deadline=meets_deadline(spec.destination_arrival_at, deadline_at),
        lead_time_min=bus.selected_lead_time_min,
        no_seat_probability=selected.no_seat_probability if selected else None,
        risk_level=selected.risk_level if selected else None,
    )


def build_snapshot(snapshot_id: uuid.UUID, scenario: ScenarioSpec) -> SnapshotResponse:
    now = scenario.computed_at
    state, next_start = service_state(now)
    in_collection = state != "outside_collection"
    # 수집 시간 밖이면 버스·후보를 비운다(계약 4.4절 buses 주석).
    bus_specs = scenario.buses if in_collection else ()
    buses = [_build_bus(spec, scenario) for spec in bus_specs]
    deadline_at = resolve_deadline(scenario.deadline, now) if scenario.deadline else None
    candidates = [
        _build_candidate(bus, spec, deadline_at) for bus, spec in zip(buses, bus_specs, strict=True)
    ]
    status, recommendation, switch = decide_alternatives(candidates, deadline_at)
    recommended = None
    if recommendation is not None:
        recommended = Recommended(
            route_id=recommendation.route_id,
            vehicle_id=recommendation.vehicle_id,
            reason_code=recommendation.reason_code,
        )
    return SnapshotResponse(
        snapshot_id=snapshot_id,
        computed_at=now,
        next_refresh_at=next_refresh_at(now, scenario.data_updated_at),
        rules_version=RULES_VERSION,
        data_updated_at=scenario.data_updated_at,
        # 수집 시간 밖에는 새 데이터가 없는 것이 정상이므로 '정보 오래됨'으로 알리지 않는다.
        stale=in_collection and is_stale(now, scenario.data_updated_at),
        service=ServiceInfo(
            state=state,
            message=None if state == "in_service" else SERVICE_OUTSIDE_MESSAGE,
            next_forecast_start_at=next_start,
        ),
        station=SnapshotStation(
            station_id=BOARD_STATION_ID,
            name=BOARD_STATION_NAME,
            direction_label=COLLECT_TARGET.direction_label,
        ),
        destination=SnapshotDestination(destination_id=DESTINATION_ID, name=DESTINATION_NAME),
        deadline=scenario.deadline,
        walk_minutes_allowed=COLLECT_TARGET.walk_minutes_allowed,
        buses=buses,
        alternatives=Alternatives(
            status=status,
            recommended=recommended,
            switch_suggested=switch,
            candidates=candidates,
        ),
    )


# ---------------------------------------------------------------------------
# scenario 목록(계약 12장)
# ---------------------------------------------------------------------------
def _g1300(
    arrival: datetime,
    issued: Mapping[int, IssuedSpec],
    *,
    seats: int | None,
    seats_at: datetime | None,
    destination_at: datetime | None,
) -> BusSpec:
    return BusSpec(
        route=ROUTE_G1300,
        vehicle=G1300_VEHICLE,
        source="arrival_1st",
        station_arrival_at=arrival,
        current_seats=seats,
        seats_updated_at=seats_at,
        destination_arrival_at=destination_at,
        issued=issued,
    )


def _bus_1306(
    arrival: datetime,
    issued: Mapping[int, IssuedSpec],
    *,
    seats: int | None,
    seats_at: datetime | None,
    destination_at: datetime | None,
) -> BusSpec:
    return BusSpec(
        route=ROUTE_1306,
        vehicle=BUS_1306_VEHICLE,
        source="arrival_2nd",
        station_arrival_at=arrival,
        current_seats=seats,
        seats_updated_at=seats_at,
        destination_arrival_at=destination_at,
        issued=issued,
    )


# 9장 예시의 입력(07:31:20 계산, 07:31:10 데이터)
_NOW = _at(7, 31, 20)
_DATA_AT = _at(7, 31, 10)
_G1300_ARRIVAL = _at(7, 38, 40)
_1306_ARRIVAL = _at(7, 42)
_G1300_15_EXAMPLE = IssuedSpec("ok", n=26, k=16, seats=7, headway_min=11)  # 0.6154
_G1300_10_EXAMPLE = IssuedSpec("ok", n=24, k=18, seats=4, headway_min=11)
_G1300_15_OK = IssuedSpec("ok", n=25, k=15, seats=7, headway_min=11)
_1306_15_EXAMPLE = IssuedSpec("ok", n=25, k=5, seats=15, headway_min=14)
_G1300_DESTINATION = _at(8, 21)
_1306_DESTINATION = _at(8, 27)


def _example_buses(
    g1300_destination: datetime | None = _G1300_DESTINATION,
    bus_1306_destination: datetime | None = _1306_DESTINATION,
) -> tuple[BusSpec, ...]:
    return (
        _g1300(
            _G1300_ARRIVAL,
            {15: _G1300_15_EXAMPLE, 10: _G1300_10_EXAMPLE},
            seats=3,
            seats_at=_DATA_AT,
            destination_at=g1300_destination,
        ),
        _bus_1306(
            _1306_ARRIVAL,
            {15: _1306_15_EXAMPLE},
            seats=14,
            seats_at=_DATA_AT,
            destination_at=bus_1306_destination,
        ),
    )


def _with_g1300_selected(selected: IssuedSpec, data_at: datetime = _DATA_AT) -> tuple[BusSpec, ...]:
    """G1300 의 10분 예보(선택되는 예보)만 바꾸고 1306 은 9장 예시 그대로."""
    return (
        _g1300(
            _G1300_ARRIVAL,
            {15: _G1300_15_OK, 10: selected},
            seats=3,
            seats_at=data_at,
            destination_at=_G1300_DESTINATION,
        ),
        _bus_1306(
            _1306_ARRIVAL,
            {15: _1306_15_EXAMPLE},
            seats=14,
            seats_at=data_at,
            destination_at=_1306_DESTINATION,
        ),
    )


def _scenario_specs() -> dict[str, ScenarioSpec]:
    example = ScenarioSpec(_NOW, _DATA_AT, "08:30", _example_buses())
    stale_data_at = _at(7, 27, 30)
    return {
        "example": example,
        # 예시와 같은 버스, 마감 없음(meetsDeadline 이 모두 null, 그래도 추천)
        "status_ok": ScenarioSpec(_NOW, _DATA_AT, None, _example_buses()),
        # 두 버스 모두 도착 15분 전 이전 → 모든 예보 not_yet, 선택 없음
        "status_not_yet": ScenarioSpec(
            _NOW,
            _DATA_AT,
            "08:45",
            (
                _g1300(_at(7, 48, 40), {}, seats=9, seats_at=_DATA_AT, destination_at=_at(8, 31)),
                _bus_1306(_at(7, 52), {}, seats=16, seats_at=_DATA_AT, destination_at=_at(8, 37)),
            ),
        ),
        "status_insufficient_cases": ScenarioSpec(
            _NOW,
            _DATA_AT,
            "08:30",
            (
                _g1300(
                    _G1300_ARRIVAL,
                    {
                        15: IssuedSpec("insufficient_cases", n=14, seats=7, headway_min=11),
                        10: IssuedSpec("insufficient_cases", n=12, seats=4, headway_min=11),
                    },
                    seats=3,
                    seats_at=_DATA_AT,
                    destination_at=_at(8, 21),
                ),
                _example_buses()[1],
            ),
        ),
        # 10분 선행시간이 공개 판정에서 탈락(판정이 끝났으므로 그 예보는 preliminary=false)
        "status_not_validated": ScenarioSpec(
            _NOW,
            _DATA_AT,
            "08:30",
            _with_g1300_selected(
                IssuedSpec("not_validated", seats=4, headway_min=11, preliminary=False)
            ),
        ),
        # 07:27:30 이후 수집이 끊겨 07:28:40 의 10분 예보를 계산하지 못함(stale=true)
        "status_stale": ScenarioSpec(
            _NOW,
            stale_data_at,
            "08:30",
            _with_g1300_selected(IssuedSpec("stale"), data_at=stale_data_at),
        ),
        "status_missing_input": ScenarioSpec(
            _NOW,
            _DATA_AT,
            "08:30",
            _with_g1300_selected(IssuedSpec("missing_input", seats=4, headway_min=None)),
        ),
        # 05:50, 첫 차가 05:57 도착(06시 전) → 그 차의 예보는 outside_hours
        "status_outside_hours": ScenarioSpec(
            _at(5, 50),
            _at(5, 49, 50),
            "07:00",
            (
                _g1300(
                    _at(5, 57, 20),
                    {},
                    seats=30,
                    seats_at=_at(5, 49, 50),
                    destination_at=_at(6, 38),
                ),
                _bus_1306(
                    _at(6, 4),
                    {15: IssuedSpec("ok", n=25, k=1, seats=36, headway_min=15)},
                    seats=35,
                    seats_at=_at(5, 49, 50),
                    destination_at=_at(6, 47),
                ),
            ),
        ),
        # 두 후보의 0석 확률이 같음 → 먼저 오는 G1300(earliest_among_equal_risk)
        "alt_recommended": ScenarioSpec(
            _NOW,
            _DATA_AT,
            "08:30",
            (
                _g1300(
                    _G1300_ARRIVAL,
                    {
                        15: IssuedSpec("ok", n=25, k=5, seats=12, headway_min=9),
                        10: IssuedSpec("ok", n=25, k=5, seats=11, headway_min=9),
                    },
                    seats=10,
                    seats_at=_DATA_AT,
                    destination_at=_at(8, 21),
                ),
                _example_buses()[1],
            ),
        ),
        # 마감 08:00 인데 두 후보 모두 08:21·08:27 도착
        "alt_no_alternative": ScenarioSpec(_NOW, _DATA_AT, "08:00", _example_buses()),
        # 구간 소요 기록 부족으로 두 후보 모두 목적지 도착 시각 없음
        "alt_arrival_unavailable": ScenarioSpec(
            _NOW, _DATA_AT, "08:30", _example_buses(None, None)
        ),
        # G1300 은 마감을 넘고 1306 은 도착 시각을 몰라 마감 판정을 끝낼 수 없음
        "alt_undecidable": ScenarioSpec(_NOW, _DATA_AT, "08:30", _example_buses(_at(8, 35), None)),
        "service_outside_collection": ScenarioSpec(_at(10, 20), _at(10, 14, 50), "08:30", ()),
        # 09:30: 수집 중이지만 예보 시간(09:00 전) 밖. 버스는 보이지만 예보는 outside_hours
        "service_outside_forecast_hours": ScenarioSpec(
            _at(9, 30),
            _at(9, 29, 50),
            "10:30",
            (
                _g1300(
                    _at(9, 37, 20),
                    {},
                    seats=20,
                    seats_at=_at(9, 29, 50),
                    destination_at=_at(10, 15),
                ),
                _bus_1306(
                    _at(9, 42),
                    {},
                    seats=25,
                    seats_at=_at(9, 29, 50),
                    destination_at=_at(10, 22),
                ),
            ),
        ),
        # 공개 판정 통과 후: 9장 예시와 같고 preliminary 만 false
        "published": ScenarioSpec(_NOW, _DATA_AT, "08:30", _example_buses(), preliminary=False),
    }


def scenario_snapshot_id(name: str) -> uuid.UUID:
    if name == "example":
        return EXAMPLE_SNAPSHOT_ID
    return uuid.uuid5(_SNAPSHOT_NAMESPACE, name)


def build_all_scenarios() -> dict[str, SnapshotResponse]:
    """scenario 이름 → 스냅샷. 입력이 규칙과 모순되면 ValueError(앱 시작 시 드러난다)."""
    return {
        name: build_snapshot(scenario_snapshot_id(name), spec)
        for name, spec in _scenario_specs().items()
    }
