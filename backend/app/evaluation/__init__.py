"""백테스트: 단순 기준(방법 A)과 사례 방식(방법 B)을 같은 운행편에 적용해 비교한다.

답하려는 질문: "지금 남은 좌석만 보는 것보다, 과거 운행 기록을 함께 보면 도착 시 0석을
더 잘 판단할 수 있었나?"
- 방법 A(단순 기준): 도착 L분 전 시점의 잔여석이 0 이면 '도착 시 0석'이라고 판단한다.
- 방법 B(사례 방식): CLAUDE.md '사례 검색과 확률'의 k ÷ n 이 경보 기준 이상이면 경보.

입력은 수집기 JSONL(<데이터폴더>/<날짜>/raw_poll.jsonl)의 위치 기록이다. DB 는 쓰지 않는다.
흐름: labeling(records → trips → labels) → backtest.label_trips → backtest.evaluate_labeled_trips
→ metrics → report.

공개 판정(pass/fail)은 여기서 하지 않는다. 규칙 값은 settings 의 상수만 읽고, 결과가 나쁘게
나와도 바꾸지 않는다.
"""
