-- =============================================================================
-- 0002_loader_role.sql : 적재 스크립트(backend/app/loader) 전용 최소 권한 역할 — 초안
--
-- !! 회의 결정 후 적용한다. 아직 어느 DB 에도 적용하지 않았다. !!
--    (지금 적재 스크립트는 테이블 소유자 postgres 로 접속한다. 이 역할로 바꾸는 것은
--     0001_init.sql 의 '회의 안건: 접속 역할 분리'에서 정한다.)
--
-- 왜: DATABASE_URL 이 새어도 피해가 적재 대상 테이블의 정해진 쓰기로 끝나게 한다.
--     소유자(postgres)는 모든 테이블을 지우거나 스키마를 바꿀 수 있다.
--
-- 이 역할이 할 수 있는 것(적재 스크립트가 쓰는 SQL 만, app/loader/store.py 기준)
--   bus_position·bus_arrival   INSERT
--       COPY 는 본인 소유 임시 테이블에 하고, INSERT ... SELECT ... ON CONFLICT DO NOTHING 으로
--       옮긴다. DO NOTHING 은 SELECT 권한이 필요 없다.
--   raw_poll                   SELECT, INSERT
--       ON CONFLICT DO NOTHING 뒤 (jsonl_file, line_no) 로 raw_poll_id 를 다시 읽는다.
--   service_day·route·station·route_station   SELECT, INSERT, 열 단위 UPDATE
--       업서트(ON CONFLICT DO UPDATE)가 기존 값을 읽고(SELECT) 아래 열만 갱신한다.
--       service_day.note(사람이 적는 특이사항)는 UPDATE 하지 않는다.
--   route_station              DELETE (기준정보에서 없어진 순번 정리, RETURNING 으로 SELECT 도 씀)
--   데이터베이스                 CONNECT, TEMPORARY (COPY 용 임시 테이블 ON COMMIT DROP)
-- 할 수 없는 것: 다른 테이블 접근, TRUNCATE, DDL, 수집 테이블의 UPDATE·DELETE(보관 정리는 소유자).
-- store.py 의 업서트 열을 바꾸면 아래 열 단위 UPDATE 도 함께 고친다.
--
-- identity 시퀀스(raw_poll.raw_poll_id 의 GENERATED ALWAYS AS IDENTITY)에는 USAGE 를 주지 않는다.
--   identity 열의 다음 값은 NextValueExpr 가 권한 확인 없이 꺼낸다(nextval_internal(..., false)).
--   serial(DEFAULT nextval(...))과 달리 INSERT 권한만으로 충분하다. 적용 뒤 확인 방법은 맨 아래.
--
-- RLS: 0001 에서 모든 테이블에 RLS 를 켜고 정책을 두지 않았으므로, 소유자가 아닌 이 역할은
--   정책이 없으면 0행만 보고 INSERT 도 거부된다. 그래서 이 역할에만, GRANT 한 명령마다 정책을 둔다
--   (SELECT·DELETE 는 USING, INSERT 는 WITH CHECK, UPDATE 는 둘 다). 업서트(ON CONFLICT DO UPDATE)는
--   INSERT·UPDATE·SELECT 정책을 모두 확인한다.
--
-- 비밀번호: 이 파일에도, SQL Editor 의 평문 ALTER ROLE ... PASSWORD 에도 넣지 않는다
--   (실행한 SQL 이 기록·로그에 남을 수 있다). 적용한 뒤 psql 로 소유자 접속해
--     \password loader_writer
--   를 쓴다(클라이언트에서 해시해 보낸다). psql 을 쓸 수 없으면 Supabase 대시보드의 비밀번호 설정
--   기능을 쓴다. 값은 팀 비밀 저장소에만 둔다.
--   접속 문자열은 풀러를 쓰면 사용자 이름이 loader_writer.<프로젝트 ref> 형식이다.
--
-- 되돌리기: backend/migrations/down/0002_down.sql
-- =============================================================================

BEGIN;

CREATE ROLE loader_writer
    LOGIN
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 3;
COMMENT ON ROLE loader_writer IS '적재 스크립트(app/loader) 전용. 0002_loader_role.sql 참고. 비밀번호는 psql \password 로 따로 설정.';

-- 데이터베이스: 접속과 임시 테이블. 데이터베이스 이름을 파일에 쓰지 않으려고 current_database() 로 준다.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO loader_writer', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO loader_writer;

-- 수집 테이블
GRANT SELECT, INSERT ON public.raw_poll TO loader_writer;
GRANT INSERT ON public.bus_position, public.bus_arrival TO loader_writer;

-- 운행일·기준정보: 업서트. UPDATE 는 store.py 가 갱신하는 열만.
GRANT SELECT, INSERT ON public.service_day, public.route, public.station, public.route_station
    TO loader_writer;
GRANT UPDATE (is_weekday, is_holiday, operation_kind, updated_at)
    ON public.service_day TO loader_writer;
GRANT UPDATE (route_name, route_type_cd, route_type_name, admin_name, region_name,
              start_station_name, end_station_name, is_collected, is_label_target, is_reserved,
              is_night, collect_interval_sec, note, source_date, updated_at)
    ON public.route TO loader_writer;
GRANT UPDATE (station_name, mobile_no, region_name, x, y, source_date, updated_at)
    ON public.station TO loader_writer;
GRANT UPDATE (station_id, is_turn_point, source_date, updated_at)
    ON public.route_station TO loader_writer;
GRANT DELETE ON public.route_station TO loader_writer;

-- RLS 정책: 이 역할에만, GRANT 한 명령마다. 다른 역할(anon·authenticated)에는 여전히 정책이 없다.
CREATE POLICY loader_writer_select ON public.raw_poll     FOR SELECT TO loader_writer USING (true);
CREATE POLICY loader_writer_insert ON public.raw_poll     FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_insert ON public.bus_position FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_insert ON public.bus_arrival  FOR INSERT TO loader_writer WITH CHECK (true);

CREATE POLICY loader_writer_select ON public.service_day FOR SELECT TO loader_writer USING (true);
CREATE POLICY loader_writer_insert ON public.service_day FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_update ON public.service_day FOR UPDATE TO loader_writer USING (true) WITH CHECK (true);

CREATE POLICY loader_writer_select ON public.route FOR SELECT TO loader_writer USING (true);
CREATE POLICY loader_writer_insert ON public.route FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_update ON public.route FOR UPDATE TO loader_writer USING (true) WITH CHECK (true);

CREATE POLICY loader_writer_select ON public.station FOR SELECT TO loader_writer USING (true);
CREATE POLICY loader_writer_insert ON public.station FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_update ON public.station FOR UPDATE TO loader_writer USING (true) WITH CHECK (true);

CREATE POLICY loader_writer_select ON public.route_station FOR SELECT TO loader_writer USING (true);
CREATE POLICY loader_writer_insert ON public.route_station FOR INSERT TO loader_writer WITH CHECK (true);
CREATE POLICY loader_writer_update ON public.route_station FOR UPDATE TO loader_writer USING (true) WITH CHECK (true);
CREATE POLICY loader_writer_delete ON public.route_station FOR DELETE TO loader_writer USING (true);

COMMIT;

-- 적용 뒤 확인(시험 DB 에서):
--   소유자로:
--   1) SELECT has_schema_privilege('loader_writer', 'public', 'CREATE'); 가 false 인지
--      (PostgreSQL 15 이상은 public 스키마 CREATE 가 기본으로 없다. Supabase PG17 예상 false).
--   loader_writer 로 접속해:
--   2) cd backend && uv run python -m app.loader load --date <날짜> 가 종료 코드 0 이고, 두 번째 실행에서
--      '이미 있음' 이 같은 수인지(identity 시퀀스 권한 없이 INSERT 되는지 포함).
--   3) uv run python -m app.loader reference 가 종료 코드 0 인지(열 단위 UPDATE·DELETE 확인).
--   4) SELECT count(*) FROM public.trip; 과 SELECT count(*) FROM public.bus_position; 이
--      permission denied 인지(다른 테이블·수집 자식 테이블 읽기 불가).
--   5) DELETE FROM public.raw_poll WHERE false; 와 UPDATE public.service_day SET note = note WHERE false;
--      가 permission denied 인지.
