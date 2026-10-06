import { Link, Outlet } from "react-router";
import styles from "./AppLayout.module.css";

export function AppLayout() {
  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Link to="/" className={styles.brand}>
          탈 수 있을까?
        </Link>
        <p className={styles.tagline}>양주 광역버스 무좌석 위험 예보</p>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
