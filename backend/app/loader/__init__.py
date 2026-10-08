"""수집기 원본 JSONL·기준정보 파일을 Supabase(PostgreSQL)에 넣는 배치(수집기와 별도 프로세스).

실행: cd backend && uv run python -m app.loader <load|reference> ... (app/loader/cli.py)
"""
