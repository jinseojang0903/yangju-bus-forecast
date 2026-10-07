import { Link } from "react-router";
import styles from "./AppLayout.module.css";
import pageStyles from "./RouteErrorPage.module.css";

/**
 * 렌더 중 예외·없는 경로를 받는 루트 errorElement.
 * 예외 메시지·스택은 화면에 보여 주지 않는다(.claude/rules/frontend.md).
 */
export function RouteErrorPage() {
  return (
    <div className={styles.shell}>
      <main className={styles.main}>
        <section className={pageStyles.box}>
          <h1 className={pageStyles.title}>화면을 보여 드리지 못했어요</h1>
          <p>주소가 잘못됐거나 일시적인 문제가 생겼어요.</p>
          <Link to="/" className={pageStyles.link}>
            처음 화면으로
          </Link>
        </section>
      </main>
    </div>
  );
}
