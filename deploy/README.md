# GBIS 수집기 배포 안내 (서버: Ubuntu, 내 PC: Windows PowerShell)

수집기는 평일 **05:30 이상 10:15 미만**(KST)에 GBIS 를 호출해 `data/collected/<날짜>/raw_poll.jsonl` 에 원본을 저장한다(2026-10-07 부터).
대상(노선마다, 도착 정류장)마다 **독립 작업자**가 자기 주기의 경계(KST 자정 기준 주기의 배수)에서 호출하고, 서로를 기다리지 않는다. 한 대상이 느리거나 실패해도 G1300 10초 호출은 밀리지 않는다.

### 수집 대상 (양주시 관할 직행좌석형 광역버스, 사용자 결정 2026-10-06)

| 노선 | routeId | 주기 | 하루 호출 | 비고 |
|---|---|---|---|---|
| G1300 | 235000092 | 10초 | 1,710 | 라벨 대상 |
| 1306 | 235000123 | 30초 | 570 | 라벨 대상 |
| 1100 | 235000085 | 40초 | 428 | 덕정역→마들역(남양주 1100 222000074 와 다름) |
| 1101 | 235000115 | 40초 | 428 | |
| 1304 | 235000118 | 40초 | 428 | |
| 1407 | 235000131 | 40초 | 428 | |
| 8300 | 235000120 | 40초 | 428 | |
| 8906 | 235000103 | 40초 | 428 | |
| G1200 | 235000104 | 40초 | 428 | |
| P9601(출근) | 233000371 | 40초 | 428 | 예약버스 |
| P9602(출근) | 233000373 | 40초 | 428 | 예약버스 |
| P9603(출근) | 235000127 | 40초 | 428 | 예약버스 |
| 도착 API 덕현초교(잠실행) | stationId 235000392 | 30초 | 570 | |

목록에만 두고 **수집하지 않는** 노선: G1300N(235000116, 심야), P9601·P9602·P9603 퇴근 편(233000372·233000374·235000128, 아침 창에 운행 안 함), 양주를 지나는 타 시 관할 3800(218000151)·8109(234001236, 사용자 확인 대기).

| API | 하루 예상 호출 | 계획 상한 | 안전 상한(닿으면 그날 멈춤) | 포털 한도 |
|---|---|---|---|---|
| 위치(buslocationservice) | 6,560 | 9,000 | 9,800 | 10,000(운영계정) |
| 도착(busarrivalservice) | 570 | 950 | 980 | 1,000 |
| 노선(busrouteservice) | discover 때만 | 4 | 4 | 1,000 |

- 하루 예상 호출이 그 API 의 계획 상한을 넘는 설정이면 `run` 이 시작하지 않는다(종료 코드 2).
- 실제 호출 수가 안전 상한에 닿으면 그 API 의 **모든 작업자**가 그날 호출을 멈춘다(`call_cap_reached`).
- JSONL 은 **전 노선**을 남긴다(하루 약 7,130줄). 운행편·라벨·DB 적재는 라벨 대상(G1300·1306)만 한다.
- 호출 타임아웃은 그 대상의 주기보다 짧다: min(10초, 주기 − 2초). G1300 은 8초.
- 운행하지 않는 시간에 GBIS 가 `{"response":{"comMsgHeader":""}}` 처럼 머리·본문 없는 응답을 주면 **빈 응답**(결과 없음과 같은 취급)이다. 실패로 세지 않고 `status` 의 `targets.<대상>.empty` 에 센다. 원본은 그대로 저장한다.
- 같은 API 에서 포털 호출량 초과 응답이 **연속 3회** 오면 그날 그 API 는 **5분에 한 번만** 시험 호출한다(`status` 의 `apis.<서비스>.throttled: true`, 로그 ERROR `quota_throttle_started`). 정상 응답이 오면 원래 주기로 돌아간다(INFO `quota_throttle_ended`).
- 수집 창 안에서 한 대상이 max(3 × 주기, 60초) 넘게 호출을 시도하지 않으면 ERROR `worker_stalled target=... since=...` 를 한 번 남긴다(기록만 하고 작업자를 재시작하지는 않는다).

이 문서의 명령은 **윈도우 PowerShell 에서 복사해 붙여 넣는** 순서로 썼다.
`<서버IP>`, `<키파일경로>` 처럼 꺾쇠로 표시한 부분은 자기 값으로 바꾼다(꺾쇠도 지운다).
`"<키파일경로>"` 처럼 따옴표 안에 있는 값은 **따옴표는 남긴다**. 경로에 공백(예: `바탕 화면`)이 있어도 동작한다.

> **먼저 확인**: `backend/app/core/settings.py` 의 `COLLECT_TARGET` 에 routeId·stationId 가 채워져 있고 커밋되어 있어야 한다.
> 비어 있으면 `once`·`run` 은 아무것도 호출하지 않고 "설정에 없다"고 출력한 뒤 끝난다.
> 채우는 방법: `backend` 폴더에서 `uv run python -m app.collector discover` 를 **하루 1번만** 실행하고, 출력 근거와 `targets.json` 을 보고 채운다(아래 '기준정보 찾기' 참고).

### 기준정보 찾기 (`discover`)

- 노선 API(버스노선 v2)는 사용자 규칙에 따라 **하루 4회 이하**로만 부른다. `discover` 1번 = 노선 2개 × (노선 검색 1 + 정류장 목록 1) = 4회다. 그래서 **discover 는 하루에 1번만** 실행한다.
- 노선 상세 API 는 부르지 않는다. 방향(잠실행)은 정류장 목록의 순번과 회차 표시(turnYn)로 판단한다.
- 노선 검색 결과가 여럿이면 추가 호출 없이 검색 결과만으로 고른다: 번호가 정확히 같고, 지역에 '양주'가 있거나 기점·종점에 '잠실'이 있는 것. 하나로 못 고르면 정류장 목록을 부르지 않고 후보를 모두 출력한 뒤 종료 코드 2 로 멈춘다.
- 오늘 노선 API 를 이미 썼으면(상태 파일 `status.json` 의 `busrouteservice` 호출 수) 남은 횟수가 4회 미만일 때 **아무것도 호출하지 않고** 멈춘다. 호출 수는 KST 0시에 다시 센다.
- 결과는 `<데이터폴더>/reference/<날짜>/` 에 저장된다. 노선 API 응답 원본 기록(`*.record.json`, 서비스 키 가림)과, 고른 결과 요약 `targets.json` 이다. `targets.json` 에는 노선별 routeId, 덕현초교 stationId·stationSeq·다음 정류장, 하차 정류장 stationId·stationSeq 가 들어 있다. DB 의 기준정보 테이블은 다음 단계에서 이 파일로 채운다.
- 같은 서비스 키로 PC 와 서버에서 각각 discover 를 돌리면 상태 파일이 달라 서로의 호출 수를 모른다. **한 곳에서 한 번만** 실행한다.
- 수집기(`run`)가 돌고 있으면 잠금 때문에 실행되지 않는다(종료 코드 3). 서비스를 시작하기 전에 실행한다.

---

## 0. 가장 중요한 경고: 한 곳에서만 돌린다

같은 서비스 키로 **서버와 PC 에서 동시에** 수집기를 돌리면 위치 API 호출이 하루 6,560 × 2 = **13,120회**(한도 10,000회),
도착 API 호출이 570 × 2 = **1,140회**(한도 1,000회)가 되어 한도를 넘는다. 한도를 넘으면 그날 남은 시간의 데이터는 다시 얻을 수 없다.
두 수집기는 상태 파일이 달라 서로의 호출 수를 모르므로, 수집기 안의 상한으로는 막을 수 없다.
서버로 옮기면 PC 의 작업 스케줄러를 끄고(10장 마지막 참고), PC 로 돌릴 때는 서버 서비스를 멈춘다.

```powershell
# 서버 서비스 멈추기 (서버에 접속한 상태에서)
sudo systemctl disable --now yangju-collector
```

---

## 1. 서버에 SSH 로 접속하기

Oracle Cloud 와 AWS Lightsail(Ubuntu) 모두 사용자 이름은 `ubuntu` 다.

```powershell
ssh -i "<키파일경로>" ubuntu@<서버IP>
```

예(경로에 공백이 있는 경우): `ssh -i "C:\Users\me\바탕 화면\keys\oracle.key" ubuntu@123.45.67.89`

"UNPROTECTED PRIVATE KEY FILE" 오류가 나면 키 파일 권한을 나만 읽게 바꾼 뒤 다시 접속한다.

```powershell
icacls "<키파일경로>" /inheritance:r
icacls "<키파일경로>" /grant:r "$($env:USERNAME):(R)"
```

접속을 끝낼 때는 `exit` 를 입력한다. 아래 2~8장의 명령은 **서버에 접속한 창**에서 실행한다(4장·9장은 PC 창).

---

## 2. 서버에 필요한 프로그램 설치

```bash
sudo apt update
sudo apt install -y git curl unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치가 끝나면 **접속을 끊었다가 다시 접속**한다(`exit` 후 1장 명령). 그다음 확인:

```bash
~/.local/bin/uv --version
```

Python 3.12 가 서버에 없어도 괜찮다. `uv sync` 가 알아서 내려받는다.

---

## 3. 저장소 내려받기

이 저장소에는 아직 원격(GitHub)이 없다. 아래 두 방법 중 하나를 고른다.

### (가) GitHub 비공개 저장소를 쓰는 경우

PC 에서 GitHub 에 **비공개(Private)** 저장소를 만들고 올린 뒤, 서버에서:

```bash
cd ~
git clone https://github.com/<계정>/<저장소이름>.git Yangju
```

비공개 저장소는 비밀번호 대신 GitHub 토큰(Personal access token)을 물어본다.
코드를 고친 뒤 서버에 반영할 때는 `cd ~/Yangju && git pull` 을 실행하고 7장의 `sudo systemctl restart yangju-collector` 를 실행한다.

### (나) zip 으로 복사하는 경우 (GitHub 없이)

PC 의 PowerShell 에서 저장소 폴더로 이동해 zip 을 만들고 서버로 보낸다.
`git archive` 는 **커밋된 내용만** 담는다. settings.py 를 고쳤다면 먼저 커밋한다. `.env` 는 담기지 않는다(4장에서 따로 보낸다).

```powershell
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju"
git archive --format=zip -o yangju.zip HEAD
scp -i "<키파일경로>" yangju.zip ubuntu@<서버IP>:~/
```

서버에서 풀기:

```bash
unzip -o ~/yangju.zip -d ~/Yangju
ls ~/Yangju
```

`backend`, `deploy`, `frontend` 등이 보이면 된다.

---

## 4. `.env` 보내기 (서비스 키)

서비스 키는 `backend/.env` 에만 있다. 저장소(zip, GitHub)에는 들어가지 않으므로 따로 보낸다.

**먼저 서버에서** 나만 읽고 쓸 수 있는 빈 파일을 만든다. 이렇게 하면 키가 들어오는 순간부터 다른 사용자가 읽을 수 없다.

```bash
install -m 600 /dev/null ~/Yangju/backend/.env
```

**그다음 PC 의 PowerShell 에서** 보낸다(이미 있는 파일에 내용을 덮어쓰므로 보통 권한 600 이 유지된다. 아래에서 다시 확인한다):

```powershell
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju"
scp -i "<키파일경로>" backend\.env ubuntu@<서버IP>:~/Yangju/backend/.env
```

서버에서 권한을 한 번 더 확인한다(`-rw-------` 이면 정상):

```bash
chmod 600 ~/Yangju/backend/.env
ls -l ~/Yangju/backend/.env
```

---

## 5. 서버 시간대 확인

```bash
timedatectl
```

수집기는 OS 시간대와 상관없이 KST(Asia/Seoul)로 계산한다. 다만 `journalctl` 등 시스템 로그 시각을 읽기 쉽도록 서울로 맞추기를 권한다.

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl
```

`Time zone: Asia/Seoul (KST, +0900)` 와 `System clock synchronized: yes` 가 보이면 된다.

---

## 6. 설치하고 한 번 시험하기

```bash
cd ~/Yangju/backend && ~/.local/bin/uv sync --frozen
```

```bash
cd ~/Yangju/backend && ~/.local/bin/uv run --frozen python -m app.collector once
```

`once` 는 지금 시각과 상관없이 수집 대상 전부를 1회씩(위치 12회 + 도착 1회) 호출하고, 각 호출의 `http=200 ok=True` 와 항목 수, 필드 존재 여부를 출력한다.
이 시험도 하루 호출 수에 포함된다(13회).

---

## 7. 서비스 등록하고 시작하기

```bash
sudo cp ~/Yangju/deploy/yangju-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yangju-collector
```

서버가 재부팅되어도 자동으로 다시 시작된다. 코드를 바꾼 뒤에는:

```bash
sudo systemctl restart yangju-collector
```

---

## 7-1. 서버에 새 코드 반영하기

코드(예: 수집 창·주기 변경)를 커밋한 뒤 서버에 반영한다. 데이터(`data/`)와 `.env` 는 그대로 남는다.
수집 창 안(평일 05:30~10:15)에 재시작하면 재시작하는 몇 초 동안 호출이 빠지므로, 가능하면 창 밖에서 한다.

### (가) git 방식 (3장 (가)로 받은 경우)

서버에서:

```bash
cd ~/Yangju && git pull
cd ~/Yangju/backend && ~/.local/bin/uv sync --frozen
sudo systemctl restart yangju-collector
```

### (나) zip 방식 (3장 (나)로 받은 경우)

PC 의 PowerShell 에서 새 zip 을 만들어 보낸다(**커밋된 내용만** 담긴다):

```powershell
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju"
git archive --format=zip -o yangju.zip HEAD
scp -i "<키파일경로>" yangju.zip ubuntu@<서버IP>:~/
```

서버에서 덮어써 풀고 재시작한다(`-o` 는 묻지 않고 덮어쓰기. `data/`·`backend/.env` 는 zip 에 없으므로 그대로 남는다):

```bash
unzip -o ~/yangju.zip -d ~/Yangju
cd ~/Yangju/backend && ~/.local/bin/uv sync --frozen
sudo systemctl restart yangju-collector
```

### 서비스 파일(`deploy/yangju-collector.service`)도 바뀐 경우

위 (가)·(나) 다음에 한 번 더 복사하고 다시 읽힌다(이번 전 노선 변경에서는 설명 줄만 바뀌었다):

```bash
sudo cp ~/Yangju/deploy/yangju-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart yangju-collector
```

### 반영 확인

서비스가 다시 떴는지:

```bash
systemctl status yangju-collector --no-pager
journalctl -u yangju-collector -n 20 --no-pager
```

새 설정이 들어갔는지(한 줄 JSON 의 `config`):

```bash
cd ~/Yangju/backend && ~/.local/bin/uv run --frozen python -m app.collector status
```

`config` 에서 아래를 확인한다(실제 출력은 한 줄이다. 보기 좋게 줄였다).

- `"window":"05:30-10:15"`
- `"intervals"` 에 `"location:G1300":10`, `"location:1306":30`, 나머지 노선 `40`, `"arrival:덕현초교":30` 이 모두 13개
- `"apis":{"buslocationservice":{"planned":6560,"daily_limit":10000,"safe_limit":9800,"planned_max":9000},"busarrivalservice":{"planned":570,"daily_limit":1000,"safe_limit":980,"planned_max":950}}`
- `"not_collected":["G1300N","P9601(퇴근)","P9602(퇴근)","P9603(퇴근)","3800","8109"]`

수집 창 안에서 한 번 이상 돈 뒤에는 실행 중인 프로세스가 남긴 `intervals`·`planned_daily_calls`·`limits` 도 같은 값인지, `targets` 에 대상 13개가 다 있는지 본다.

---

## 8. 잘 돌고 있는지 확인하기

서비스 상태(`active (running)` 이면 정상):

```bash
systemctl status yangju-collector --no-pager
```

최근 로그 50줄:

```bash
journalctl -u yangju-collector -n 50 --no-pager
```

한 줄 상태(JSON). `running`, `in_window_now`, `last_success_at`, `apis` 의 호출 수(`quota_exceeded` 가 0 인지), `last_error` 를 본다.
`targets` 에는 대상별 주기(`interval_sec`), 오늘 `calls`·`success`·`failure`, 늦어서 건너뛴 주기 `skipped_cycles`, 상한으로 안 부른 `cap_skips`, 호출량 초과 감속으로 안 부른 `throttle_skips`, 빈 응답 `empty`(성공에 포함), `last_success_at` 이 있다. G1300 의 `skipped_cycles` 가 0 근처인지 본다:

```bash
cd ~/Yangju/backend && ~/.local/bin/uv run --frozen python -m app.collector status
```

오늘 저장된 줄 수(창 안에서는 1분에 약 25줄씩 늘어난다: G1300 6줄, 1306 2줄, 40초 노선 10개 15줄, 도착 2줄. 하루 끝나면 7,130줄 근처):

```bash
wc -l ~/Yangju/data/collected/$(TZ=Asia/Seoul date +%F)/raw_poll.jsonl
```

파일 로그(회전, 최대 5MB × 10개):

```bash
tail -n 50 ~/Yangju/data/logs/collector.log
```

---

## 9. 백업: 매일 수집이 끝난 뒤(10시 이후) PC 로 내려받기

서버 디스크가 망가지면 그날 데이터는 다시 얻을 수 없다. **매일 10시 이후** PC 에서 하루치를 내려받는다.

```powershell
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju"
New-Item -ItemType Directory -Force .\backup | Out-Null
scp -i "<키파일경로>" -r ubuntu@<서버IP>:~/Yangju/data/collected/<날짜> .\backup\
```

`<날짜>` 는 `2026-10-07` 형식이다. 상태 파일도 함께 보관하려면:

```powershell
scp -i "<키파일경로>" ubuntu@<서버IP>:~/Yangju/data/collected/status.json .\backup\status-<날짜>.json
```

`backup` 폴더는 저장소 안에 있으므로 커밋하지 않도록 주의한다(필요하면 저장소 밖 폴더로 바꿔도 된다).

### 9-1. DB 적재: 수집이 끝난 뒤(평일 10:30) Supabase 에 넣기

적재 스크립트(`app.loader`)는 수집기와 따로 도는 배치다. 그날 JSONL 에서 **G1300·1306 위치와 덕현초교 도착 줄만** 골라 `service_day` → `raw_poll` → `bus_position` → `bus_arrival` 에 날짜마다 한 트랜잭션으로 넣는다(나머지 10개 노선은 JSONL 에만 남는다). 같은 날을 여러 번 돌려도 행 수가 같으므로, 실패했거나 수집 중에 돌렸으면 그냥 다시 돌리면 된다(쓰는 중이던 마지막 줄은 다음 실행에서 들어간다). `backend/.env` 의 `DATABASE_URL` 을 쓰고, 접속은 항상 SSL(`sslmode=require` 이상)이다. 서버에서는 평일 10:30 에 한 번 돌릴 예정이며 타이머(systemd timer) 등록은 메인이 따로 한다. 손으로 돌릴 때:

```bash
cd ~/Yangju/backend
~/.local/bin/uv run --frozen python -m app.loader load --date $(TZ=Asia/Seoul date +%F) --dry-run   # DB 없이 건수만
~/.local/bin/uv run --frozen python -m app.loader load --date $(TZ=Asia/Seoul date +%F)
~/.local/bin/uv run --frozen python -m app.loader reference      # 기준정보(route·station·route_station). discover 를 다시 했을 때만
```

밀린 날은 `load --from 2026-10-07 --to 2026-10-10` 처럼 한 번에 넣는다(파일이 없는 날은 `service_day` 에 `operation_kind=none` 만 남긴다). 종료 코드: 0 성공, 1 사용법(인자·폴더), 2 설정 누락(`DATABASE_URL` 없음, `DATABASE_SSLROOTCERT` 파일 없음, 대상 ID 없음), 3 적재 실패(그날 전체 롤백, 뒤 날짜는 하지 않음).

**서버 인증서 확인(verify-full)**: 기본(`sslmode=require`)은 통신을 암호화하지만 상대가 진짜 Supabase 서버인지는 확인하지 않는다. Supabase 대시보드(Project Settings > Database > SSL Configuration)에서 CA 인증서(`prod-ca-2021.crt` 등)를 내려받아 서버의 저장소 밖 경로(예: `~/.config/yangju/supabase-ca.crt`)에 두고, `backend/.env` 에 `DATABASE_SSLROOTCERT=<그 경로>` 를 넣으면 적재 스크립트가 `sslmode=verify-full` 로 서버 인증서와 호스트 이름까지 확인한다. 경로를 넣었는데 파일이 없으면 종료 코드 2 로 멈춘다. 접속 문자열이나 `PGSSLMODE` 에 이미 `verify-ca`·`verify-full` 이 있으면 그 값을 쓴다.

**다시 넣을 때 주의**
- 적재는 키가 이미 있으면 넣지 않는다(`ON CONFLICT DO NOTHING`). 그래서 **해석 규칙(app/loader)을 바꾼 뒤 같은 날을 다시 돌려도 이미 들어간 행은 바뀌지 않는다.** 새 규칙으로 다시 만들려면 그날 행을 먼저 지운다(자식 먼저: `bus_position`·`bus_arrival` → `raw_poll`, `jsonl_file = '<날짜>/raw_poll.jsonl'` 기준). 원본은 JSONL 이므로 지워도 다시 만들 수 있다.
- `--exclude-trial` 로 넣은 날을 나중에 기본(시운전 포함)으로 다시 돌리면 빠졌던 시운전 줄이 추가된다(반대로는 지워지지 않는다).
- `reference` 는 정류장 목록에 건너뛴 항목이 있는 노선은 기준정보에서 없어진 순번을 지우지 않고 경고만 낸다. `--dry-run` 으로 노선별로 남길 순번을 먼저 본다.

---

## 10. 서버 준비 전까지 이 PC 에서 임시로 돌리기

0장 경고: 서버와 동시에 돌리지 않는다.

### 먼저: 수집 폴더를 OneDrive 밖으로 옮기기

이 저장소는 OneDrive 폴더 안에 있다. 수집기는 매분 파일을 쓰고(fsync), 상태 파일을 바꿔치기하고(`os.replace`), 잠금 파일을 잡는다.
OneDrive 가 같은 파일을 동기화하려고 붙잡으면 쓰기가 실패할 수 있으므로, PC 에서 돌릴 때는 수집 폴더를 OneDrive 밖으로 지정한다.

`backend\.env` 를 메모장으로 열고 아래 한 줄을 넣는다(이미 `COLLECT_DATA_DIR=` 줄이 있으면 그 줄을 고친다).

```
COLLECT_DATA_DIR=C:\yangju-data
```

폴더는 수집기가 알아서 만든다. 이렇게 지정하면 원본은 `C:\yangju-data\<날짜>\raw_poll.jsonl`, 상태는 `C:\yangju-data\status.json`, 로그는 `C:\yangju-data\logs\collector.log` 에 쌓인다.
(서버에서는 지정하지 않는다. 비워 두면 저장소의 `data/collected`, `data/logs` 를 쓴다.)

### 직접 실행 (창을 닫으면 멈춘다)

```powershell
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju\backend"
uv run python -m app.collector run --exit-after-window
```

`--exit-after-window` 의 동작:
- 평일 05:30 전에 실행하면 05:30 까지 기다렸다가 수집을 시작하고, 10:15 가 되면 스스로 종료한다(마지막 호출은 10:14:50 G1300).
- 평일 05:30~10:15 사이에 실행하면 대상마다 바로 다음 자기 주기 경계부터 수집하고, 10:15 에 종료한다.
- 주말이거나 평일 10:15 이후에 실행하면 아무것도 호출하지 않고 바로 종료한다.

멈추려면 `Ctrl+C` 를 누른다.

### 작업 스케줄러에 등록 (평일 05:25 자동 실행)

PowerShell 을 **관리자 권한**으로 열고 실행한다.

```powershell
$backend = "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju\backend"
$uv = (Get-Command uv).Source
$action = New-ScheduledTaskAction -Execute $uv -Argument "run python -m app.collector run --exit-after-window" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "05:25"
$taskSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "YangjuCollector" -Action $action -Trigger $trigger -Settings $taskSettings -Description "GBIS 수집기 (평일 05-10시)"
```

옵션 설명:
- `-WakeToRun`: 절전 상태면 깨워서 실행한다.
- `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`: 기본값은 "전원이 연결되어 있을 때만 시작, 배터리로 바뀌면 중지"다. 노트북이 배터리로 바뀌어도 수집이 멈추지 않게 이 두 옵션을 넣었다. 전원 연결 시에만 돌리고 싶으면 두 옵션을 지운다.
- `-StartWhenAvailable`: 05:25 에 PC 가 꺼져 있었다면 켜진 뒤 바로 실행한다(10:15 이후면 바로 끝난다).
- 이미 04:55 로 등록해 두었다면 아래로 시각만 바꾼다.

```powershell
Set-ScheduledTask -TaskName "YangjuCollector" -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "05:25")
```
- 기본 설정은 "사용자가 로그온한 경우에만 실행"이다. 화면 잠금 상태는 괜찮지만 로그아웃하면 실행되지 않는다.

시험 실행과 확인:

```powershell
Start-ScheduledTask -TaskName "YangjuCollector"
Get-ScheduledTaskInfo -TaskName "YangjuCollector"
cd "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju\backend"
uv run python -m app.collector status
```

서버로 옮긴 뒤 PC 작업을 끄기(지우기):

```powershell
Disable-ScheduledTask -TaskName "YangjuCollector"
Unregister-ScheduledTask -TaskName "YangjuCollector" -Confirm:$false
```

### 절전 막기

PC 가 수집 중에 절전으로 들어가면 그동안의 데이터가 빠진다. 전원 연결 시 절전·최대 절전을 끈다.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT
```

마지막 두 줄은 "절전 해제 타이머 허용"을 켜서 `-WakeToRun` 이 동작하게 한다.

원래대로 되돌리기(윈도우 기본값 기준: 절전 30분, 최대 절전 사용 안 함). 바꾸기 전 값을 알고 있으면 그 값을 넣는다.

```powershell
powercfg /change standby-timeout-ac 30
powercfg /change hibernate-timeout-ac 0
```

위의 `COLLECT_DATA_DIR=C:\yangju-data` 를 지정했다면 PC 의 원본은 `C:\yangju-data\<날짜>\raw_poll.jsonl`, 로그는 `C:\yangju-data\logs\collector.log` 에 쌓인다.

---

## 11. 명령 한눈에 보기

모두 `backend` 폴더에서 실행한다(서버에서는 `uv` 대신 `~/.local/bin/uv`, `run` 뒤에 `--frozen`).

| 명령 | 하는 일 |
|---|---|
| `uv run python -m app.collector discover` | 노선·정류장 ID 를 찾아 근거 출력, `reference/<날짜>/` 에 원본 기록과 `targets.json` 저장. 노선 API 하루 4회 이하라 **하루 1번만** 실행 |
| `uv run python -m app.collector once` | 수집 대상 전부를 1회씩 수집하고 요약 출력 |
| `uv run python -m app.collector run` | 평일 05:30~10:15 대상마다 독립 작업자로 자기 주기에 수집(G1300 10초, 1306·도착 30초, 나머지 40초. 계속 실행). 하루 예상 호출이 API 별 계획 상한(위치 9,000, 도착 950)을 넘는 설정이면 시작하지 않음(종료 코드 2) |
| `uv run python -m app.collector run --exit-after-window` | 그날 창이 끝나면 종료(작업 스케줄러용) |
| `uv run python -m app.collector run --trial-until HH:MM [--interval-sec N] [--only-route NAME ...] [--skip-arrival]` | 시운전. 수집 창·요일을 무시하고 오늘 그 시각(KST) **전**까지 수집 대상 전부를 **각자 정식 주기**로 부른 뒤, 그 시각에 모든 작업자를 멈추고 종료한다(예: `--trial-until 11:30` 이면 G1300 은 11:29:50 호출이 마지막). `--interval-sec N` 을 주면 모든 대상을 N초마다 부른다. 경계는 KST 자정 기준 N초의 배수다(40초면 10:20:00, 10:20:40, 10:21:20 …). N 은 10~60 이고 86400 의 약수여야 한다(10·15·20·30·40·45·48·60 등). `--only-route G1300` 처럼 수집 노선을 골라 위치만 부를 수 있고(여러 번 쓸 수 있음, 수집 노선 이름만. 예: `--only-route "P9601(출근)"`), `--skip-arrival` 이면 도착 API 를 부르지 않는다. 세 옵션 모두 `--trial-until` 과 함께일 때만 쓸 수 있다. 기록은 `mode=trial` 로 남아 평가에서 제외한다. 호출 수는 하루 한도에 포함된다(전 노선 10분 시운전이면 위치 약 230회, 도착 20회). 지난 시각·형식 오류·허용되지 않는 N·모르는 노선이면 종료 코드 2 |
| `uv run python -m app.collector status` | 오늘 상태 한 줄 JSON |
| `uv run python -m app.collector save-fixture` | 실제 응답을 `backend/tests/fixtures/gbis/` 에 저장(키 제거) |
| `uv run python -m app.loader load --date YYYY-MM-DD [--dry-run] [--exclude-trial]` | 그날 JSONL 의 G1300·1306 위치·덕현초교 도착을 DB 에 적재(9-1장). `--from A --to B` 로 기간 적재 |
| `uv run python -m app.loader reference [--date YYYY-MM-DD] [--dry-run]` | 기준정보 폴더(기본: 가장 최근)로 route·station·route_station 업서트 |

종료 코드: 0 정상, 1 호출 실패 있음, 2 설정 누락(서비스 키·routeId·stationId)·잘못된 옵션·정식 수집 설정이 계획 상한을 넘음, 또는 discover 가 후보를 못 골랐거나 노선 API 하루 상한으로 멈춤, 3 이미 실행 중.

---

## 12. 문제 해결

**아무 로그도 안 나오고 조용하다**
수집 창 밖(주말, 평일 10:15~다음 날 05:30)이면 정상이다. 로그에 `outside_window sleep_until=...` 이 한 번 찍히고 다음 창까지 잠든다.
`status` 의 `in_window_now` 가 `false` 인지 확인한다. 평일 공휴일(예: 2026-10-09 한글날)에도 수집하며, 기록에 `is_holiday: true` 로 표시된다.

**서비스 키 오류**
로그나 `once` 출력에 아래 같은 메시지가 보이면 키 문제다.

```
poll_failed api=getBusLocationListv2 ... error=게이트웨이 오류: SERVICE_KEY_IS_NOT_REGISTERED_ERROR (returnReasonCode=30)
```

- `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`: 키가 틀렸거나, 해당 API(버스위치정보 v2·버스도착정보 v2·버스노선 v2)를 활용신청하지 않았거나, 승인 직후라 아직 반영되지 않았다(반영에 1시간 이상 걸리기도 한다).
- `LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR`(또는 `returnReasonCode=22`): 포털 하루 호출 한도를 넘었다. 수집기는 이를 따로 알아보고 그날 처음 받았을 때 ERROR `GBIS_DAILY_QUOTA_EXCEEDED service=...` 를 남기며, `status` 의 `apis.<서비스>.quota_exceeded` 에 횟수를 센다. 다른 곳(PC·서버)에서 같은 키로 돌고 있지 않은지, 포털의 그 API 한도(위치는 운영계정 10,000회)가 맞는지 확인한다.
- `config_missing name=GBIS_SERVICE_KEY`: `backend/.env` 가 없거나 `GBIS_SERVICE_KEY=` 가 비어 있다. 4장을 다시 한다.
- 키는 공공데이터포털 마이페이지의 "일반 인증키(Decoding)" 값을 권장한다. Encoding 값(`%` 포함)을 넣어도 수집기가 한 번 디코딩해서 쓴다.

**`already_running` / 종료 코드 3**
같은 데이터 폴더에서 수집기가 이미 돌고 있다. 서버에서는 `systemctl status yangju-collector` 로, PC 에서는 작업 관리자에서 확인한다.
서비스가 돌고 있을 때 `once`·`save-fixture` 를 실행하면 이 메시지가 나온다(정상). 시험이 필요하면 서비스를 잠깐 멈춘다.

**`call_cap_reached`**
그 API 의 오늘 호출 수가 안전 상한(위치 9,800회, 도착 980회)에 닿아 그 API 의 모든 작업자가 호출을 멈췄다. 다음 날 0시(KST)에 자동으로 다시 센다.

**`cycles_skipped target=...`**
그 대상의 호출 한 번이 자기 주기보다 오래 걸려 다음 주기를 건너뛰었다(몰아서 호출하지 않는다). 다른 대상에는 영향이 없다. `status` 의 `targets.<대상>.skipped_cycles` 에 센다. 가끔이면 괜찮고, 자주 보이면 네트워크를 확인한다.

**`lock_wait_slow lock=status|jsonl`**
상태 파일·JSONL 쓰기 잠금을 0.5초 넘게 기다렸다. 디스크가 느리다는 뜻이다(PC 라면 데이터 폴더가 OneDrive 동기화 중인지 본다). 자주 보이면 G1300 호출이 밀릴 수 있다.

**서비스가 계속 재시작된다**
`journalctl -u yangju-collector -n 100 --no-pager` 로 원인을 본다. `config_missing ids=...` 이면 settings.py 의 routeId·stationId 가 비어 있는 코드로 배포된 것이다.

**`status_unreadable` / `status_recovered_from_files`**
상태 파일이 깨졌거나 없어져서, 오늘 JSONL(위치·도착)과 `reference/<날짜>/` 의 원본 기록(노선)에서 API 별 호출 수를 다시 셌다는 뜻이다. 수집은 계속된다.
파일에 남지 않은 호출(호출 중 예외)은 세지 못하므로 실제보다 조금 적을 수 있다.

**discover 가 "노선 API 오늘 남은 호출 …회 < 필요 4회" 를 출력하고 멈춘다**
오늘 이미 discover 를 실행했다. 하루 4회 규칙 때문에 다시 호출하지 않는다. 이미 저장된 `reference/<날짜>/targets.json` 을 본다.

---

## 13. 나중에 할 일

- 서비스 보안 강화: 전용 사용자(예: `yangju`)로 실행하고, `ProtectSystem=strict` 와 `ReadWritePaths=`(데이터·로그 폴더, uv 캐시, `.venv`)를 함께 넣는다. 지금은 uv 캐시·`.venv` 쓰기가 막힐 위험이 있어 `NoNewPrivileges`·`PrivateTmp`·`UMask=0077` 만 넣었다.
