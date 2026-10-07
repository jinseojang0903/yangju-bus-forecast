"""app.openapi() 를 docs/openapi.json 으로 저장한다(키 정렬, 들여쓰기 2칸, UTF-8).

실행: cd backend && uv run python scripts/export_openapi.py
.env 값과 무관하게 같은 결과가 나오도록 기본 설정(.env 미사용)으로 앱을 만든다.
"""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.settings import REPO_ROOT, Settings  # noqa: E402
from app.main import create_app  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "docs" / "openapi.json"


def main() -> int:
    app = create_app(Settings(_env_file=None))
    schema = app.openapi()
    text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
