# config.py (A파트가 정의 → B, C가 import하여 사용)
# ── Ganache RPC 설정 ──────────────────────────────────────────
GANACHE_URL = "http://127.0.0.1:7545"
GANACHE_RPC_URL = GANACHE_URL  # A파트 호환용 별칭

# ── 네트워크 설정 ─────────────────────────────────────────────
CHAIN_ID = 5777          # Ganache 로컬 네트워크 기본 Chain ID
NETWORK_NAME = "Ganache Local"

# ── FDS 서버 연동 설정 (2주차 B파트 연동용) ──────────────────
FDS_SERVER_URL = "http://127.0.0.1:8000"
FDS_ENDPOINT   = "/predict"          # POST /predict

# ── 블록 읽기 설정 ────────────────────────────────────────────
READ_BLOCK_COUNT = 5                 # 최근 N개 블록 읽기

# ── 로깅 설정 ─────────────────────────────────────────────────
LOG_LEVEL = "INFO"
