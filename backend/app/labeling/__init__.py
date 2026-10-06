"""F06 운행편 재구성과 정답 라벨 '도착 상태 0석'.

입력은 수집기 JSONL(<데이터폴더>/<날짜>/raw_poll.jsonl)의 위치 API 기록이다. DB 는 아직 쓰지 않는다.
흐름: records.load_raw_poll → trips.build_trips → labels.label_trip → report.
"""
