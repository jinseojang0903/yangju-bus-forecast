"""운행편 재구성·정답 라벨 계산 상수.

TODO(backend-dev/2026-10-06): 메인이 app/core/settings.py 로 옮긴다. 같은 시각에 다른 작업자가
  settings.py 를 고치고 있어 이 모듈에 임시로 둔다. 옮긴 뒤에는 이 모듈이 settings 의 값을
  다시 내보내기만 하게 바꾸고, 숫자를 여기에 다시 쓰지 않는다.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# GBIS 버스위치정보 v2 의 stateCd. 0 교차로 통과(정류장 사이 이동 중), 1 정류소 도착, 2 정류소 출발.
STATE_PASSING: Final = 0
STATE_ARRIVED: Final = 1
STATE_DEPARTED: Final = 2

# 운행편 나누기 기준(운영값. '바꾸지 않는 규칙'이 아니다).
# 같은 차량(날짜·노선·vehId)의 기록에서 앞 기록과 이만큼(초)보다 더 벌어지면 새 운행편으로 본다.
# 근거: 수집 실패나 차고지 대기 뒤에 다시 나타난 차량을 같은 운행편으로 잇지 않기 위해서다.
TRIP_SPLIT_GAP_SEC: Final = 30 * 60
# 정류장 순번이 앞 기록보다 이만큼 이상 줄면 새 운행편으로 본다(종점 회차 뒤 기점에서 다시 시작).
# G1300 은 순번 1~51(30 이 잠실 회차), 1306 은 26 이 회차다. 한 바퀴 끝에서 기점으로 돌아가면
# 순번이 수십 줄어든다. 위치 보정으로 1~2 순번 뒤로 튀는 것은 같은 운행편으로 둔다.
TRIP_SPLIT_SEQ_DROP: Final = 10

# 덕현초교.덕고개 잠실행(COLLECT_TARGET.board_station_id = 235000392)의 노선별 정류장 순번.
# 근거: 2026-10-06 discover 결과(data/collected/reference/2026-10-06/targets.json)와
# settings.COLLECT_TARGET 주석. 리포트가 위치 기록의 stationId 와 targets.json 으로 다시 확인한다.
TARGET_STATION_SEQ_BY_ROUTE_NAME: Final[Mapping[str, int]] = MappingProxyType(
    {"G1300": 13, "1306": 11}
)
