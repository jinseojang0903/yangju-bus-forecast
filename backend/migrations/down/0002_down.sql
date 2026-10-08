-- 경고: 실행 전에 대상 DB(Supabase 프로젝트·접속 주소)를 반드시 확인한다.
-- 확인했으면 BEGIN 바로 아래의 `SET LOCAL app.confirm_down_0002 = 'yes';` 줄 주석을 풀고 실행한다.
-- 없으면 가드가 오류를 내고 아무것도 바꾸지 않는다. SET LOCAL 이라 트랜잭션이 끝나면 값이 남지 않는다
-- (같은 창에서 다른 down 파일을 실수로 실행해도 통과하지 않게 파일마다 변수 이름이 다르다).
-- =============================================================================
-- down/0002_down.sql : 0002_loader_role.sql 되돌리기. loader_writer 역할과 그 정책·권한을 지운다.
-- 테이블과 데이터는 건드리지 않는다. 이 역할로 접속 중인 세션이 있으면 DROP ROLE 이 실패하므로
-- 적재 스크립트(타이머)를 먼저 멈춘다. 적재 스크립트의 DATABASE_URL 은 소유자 접속으로 되돌린다.
-- DROP OWNED BY 는 쓰지 않는다(예상 밖 객체를 조용히 지우지 않게). 이 역할이 소유한 객체가 있으면
-- 멈추고 알린다. 그때는 소유자를 옮길지 지울지 사람이 정한다.
-- =============================================================================

BEGIN;
-- SET LOCAL app.confirm_down_0002 = 'yes';

DO $$
BEGIN
    IF coalesce(current_setting('app.confirm_down_0002', true), '') <> 'yes' THEN
        RAISE EXCEPTION '0002_down 중단: 대상 DB 를 확인한 뒤 BEGIN 아래의 SET LOCAL app.confirm_down_0002 = ''yes''; 주석을 푼다.';
    END IF;
END
$$;

-- 정책 이름은 0002_loader_role.sql 과 같다(테이블 × 명령).
DROP POLICY IF EXISTS loader_writer_select ON public.raw_poll;
DROP POLICY IF EXISTS loader_writer_insert ON public.raw_poll;
DROP POLICY IF EXISTS loader_writer_insert ON public.bus_position;
DROP POLICY IF EXISTS loader_writer_insert ON public.bus_arrival;
DROP POLICY IF EXISTS loader_writer_select ON public.service_day;
DROP POLICY IF EXISTS loader_writer_insert ON public.service_day;
DROP POLICY IF EXISTS loader_writer_update ON public.service_day;
DROP POLICY IF EXISTS loader_writer_select ON public.route;
DROP POLICY IF EXISTS loader_writer_insert ON public.route;
DROP POLICY IF EXISTS loader_writer_update ON public.route;
DROP POLICY IF EXISTS loader_writer_select ON public.station;
DROP POLICY IF EXISTS loader_writer_insert ON public.station;
DROP POLICY IF EXISTS loader_writer_update ON public.station;
DROP POLICY IF EXISTS loader_writer_select ON public.route_station;
DROP POLICY IF EXISTS loader_writer_insert ON public.route_station;
DROP POLICY IF EXISTS loader_writer_update ON public.route_station;
DROP POLICY IF EXISTS loader_writer_delete ON public.route_station;

DO $$
DECLARE
    role_oid oid;
    owned_count integer;
BEGIN
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname = 'loader_writer';
    IF role_oid IS NULL THEN
        RETURN;  -- 이미 없다
    END IF;

    -- 이 역할이 소유한 객체(모든 데이터베이스)가 있으면 멈춘다.
    SELECT count(*) INTO owned_count
    FROM pg_shdepend
    WHERE refclassid = 'pg_authid'::regclass AND refobjid = role_oid AND deptype = 'o';
    IF owned_count > 0 THEN
        RAISE EXCEPTION '0002_down 중단: loader_writer 가 소유한 객체가 %개 있다. 소유자를 옮기거나 지운 뒤 다시 실행한다.', owned_count;
    END IF;

    -- 0002 에서 준 권한을 회수한다(열 단위 UPDATE 도 테이블 단위 REVOKE ALL 로 함께 회수된다).
    REVOKE ALL ON public.raw_poll, public.bus_position, public.bus_arrival,
                  public.service_day, public.route, public.station, public.route_station
        FROM loader_writer;
    REVOKE ALL ON SCHEMA public FROM loader_writer;
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM loader_writer', current_database());

    -- 남은 권한이 있으면 DROP ROLE 이 오류로 멈추고 트랜잭션 전체가 되돌려진다.
    DROP ROLE loader_writer;
END
$$;

COMMIT;
