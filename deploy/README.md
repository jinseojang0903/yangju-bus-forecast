# GBIS 수집기 배포 안내 (서버: Ubuntu, 내 PC: Windows PowerShell)

수집기는 평일 05:00~10:00(KST)에 1분마다 GBIS를 호출해 `data/collected/<날짜>/raw_poll.jsonl` 에 원본을 저장한다.
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

같은 서비스 키로 **서버와 PC 에서 동시에** 수집기를 돌리면 위치 API 호출이 하루 600 × 2 = **1,200회**가 되어
한도(1,000회)를 넘는다. 한도를 넘으면 그날 남은 시간의 데이터는 다시 얻을 수 없다.
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

`once` 는 지금 시각과 상관없이 위치 2회 + 도착 1회를 호출하고, 각 호출의 `http=200 ok=True` 와 항목 수, 필드 존재 여부를 출력한다.
이 시험도 하루 호출 수에 포함된다(3회).

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

## 8. 잘 돌고 있는지 확인하기

서비스 상태(`active (running)` 이면 정상):

```bash
systemctl status yangju-collector --no-pager
```

최근 로그 50줄:

```bash
journalctl -u yangju-collector -n 50 --no-pager
```

한 줄 상태(JSON). `running`, `in_window_now`, `last_success_at`, `apis` 의 호출 수, `last_error` 를 본다:

```bash
cd ~/Yangju/backend && ~/.local/bin/uv run --frozen python -m app.collector status
```

오늘 저장된 줄 수(창 안에서는 1분에 3줄씩 늘어난다. 하루 끝나면 900줄 근처):

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
- 평일 05:00 전에 실행하면 05:00 까지 기다렸다가 수집을 시작하고, 10:00 이 되면 스스로 종료한다.
- 평일 05:00~10:00 사이에 실행하면 바로 다음 분부터 수집하고, 10:00 에 종료한다.
- 주말이거나 평일 10:00 이후에 실행하면 아무것도 호출하지 않고 바로 종료한다.

멈추려면 `Ctrl+C` 를 누른다.

### 작업 스케줄러에 등록 (평일 04:55 자동 실행)

PowerShell 을 **관리자 권한**으로 열고 실행한다.

```powershell
$backend = "D:\OneDrive - YoungLimWonSoftLab\바탕 화면\Yangju\backend"
$uv = (Get-Command uv).Source
$action = New-ScheduledTaskAction -Execute $uv -Argument "run python -m app.collector run --exit-after-window" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "04:55"
$taskSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "YangjuCollector" -Action $action -Trigger $trigger -Settings $taskSettings -Description "GBIS 수집기 (평일 05-10시)"
```

옵션 설명:
- `-WakeToRun`: 절전 상태면 깨워서 실행한다.
- `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`: 기본값은 "전원이 연결되어 있을 때만 시작, 배터리로 바뀌면 중지"다. 노트북이 배터리로 바뀌어도 수집이 멈추지 않게 이 두 옵션을 넣었다. 전원 연결 시에만 돌리고 싶으면 두 옵션을 지운다.
- `-StartWhenAvailable`: 04:55 에 PC 가 꺼져 있었다면 켜진 뒤 바로 실행한다(10시 이후면 바로 끝난다).
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
| `uv run python -m app.collector once` | 지금 1회 수집하고 요약 출력 |
| `uv run python -m app.collector run` | 평일 05~10시 1분마다 수집(계속 실행) |
| `uv run python -m app.collector run --exit-after-window` | 그날 창이 끝나면 종료(작업 스케줄러용) |
| `uv run python -m app.collector run --trial-until HH:MM [--interval-sec N]` | 시운전. 수집 창·요일을 무시하고 오늘 그 시각(KST) **전**까지 N초(기본 60)마다 수집한 뒤 종료(예: `--trial-until 11:30` 이면 11:29 호출이 마지막). 경계는 KST 자정 기준 N초의 배수다(40초면 10:20:00, 10:20:40, 10:21:20 …). N 은 20~60 이고 86400 의 약수여야 한다(20·30·40·45·48·60 등). `--interval-sec` 는 `--trial-until` 과 함께일 때만 쓸 수 있다. 기록은 `mode=trial`, `interval_sec=N` 으로 남아 평가에서 제외한다. 호출 수는 하루 한도에 포함된다(40초면 5시간 기준 위치 API 900회). 지난 시각·형식 오류·허용되지 않는 N 이면 종료 코드 2 |
| `uv run python -m app.collector status` | 오늘 상태 한 줄 JSON |
| `uv run python -m app.collector save-fixture` | 실제 응답을 `backend/tests/fixtures/gbis/` 에 저장(키 제거) |

종료 코드: 0 정상, 1 호출 실패 있음, 2 설정 누락(서비스 키·routeId·stationId) 또는 discover 가 후보를 못 골랐거나 노선 API 하루 상한으로 멈춤, 3 이미 실행 중.

---

## 12. 문제 해결

**아무 로그도 안 나오고 조용하다**
수집 창 밖(주말, 평일 10:00~다음 날 05:00)이면 정상이다. 로그에 `outside_window sleep_until=...` 이 한 번 찍히고 다음 창까지 잠든다.
`status` 의 `in_window_now` 가 `false` 인지 확인한다. 평일 공휴일(예: 2026-10-09 한글날)에도 수집하며, 기록에 `is_holiday: true` 로 표시된다.

**서비스 키 오류**
로그나 `once` 출력에 아래 같은 메시지가 보이면 키 문제다.

```
poll_failed api=getBusLocationListv2 ... error=게이트웨이 오류: SERVICE_KEY_IS_NOT_REGISTERED_ERROR (returnReasonCode=30)
```

- `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`: 키가 틀렸거나, 해당 API(버스위치정보 v2·버스도착정보 v2·버스노선 v2)를 활용신청하지 않았거나, 승인 직후라 아직 반영되지 않았다(반영에 1시간 이상 걸리기도 한다).
- `LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR`: 하루 호출 한도를 넘었다. 다른 곳(PC·서버)에서 같은 키로 돌고 있지 않은지 확인한다.
- `config_missing name=GBIS_SERVICE_KEY`: `backend/.env` 가 없거나 `GBIS_SERVICE_KEY=` 가 비어 있다. 4장을 다시 한다.
- 키는 공공데이터포털 마이페이지의 "일반 인증키(Decoding)" 값을 권장한다. Encoding 값(`%` 포함)을 넣어도 수집기가 한 번 디코딩해서 쓴다.

**`already_running` / 종료 코드 3**
같은 데이터 폴더에서 수집기가 이미 돌고 있다. 서버에서는 `systemctl status yangju-collector` 로, PC 에서는 작업 관리자에서 확인한다.
서비스가 돌고 있을 때 `once`·`save-fixture` 를 실행하면 이 메시지가 나온다(정상). 시험이 필요하면 서비스를 잠깐 멈춘다.

**`call_cap_reached`**
그 API 의 오늘 호출 수가 안전 상한(980회)에 닿아 호출을 멈췄다. 다음 날 0시(KST)에 자동으로 다시 센다.

**`cycles_skipped`**
한 주기가 1분 넘게 걸려 다음 주기를 건너뛰었다(몰아서 호출하지 않는다). 가끔이면 괜찮고, 자주 보이면 네트워크를 확인한다.

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
