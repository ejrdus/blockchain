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

# ── Ganache → 이더리움 Feature 스케일링 ─────────────────────
# 로컬 Ganache의 feature 분포를 Kaggle 실 이더리움 학습 데이터
# 스케일로 변환한다. 실제 이더리움 메인넷 배포 시 False로 변경.
IS_LOCAL_GANACHE = True

# ── Ganache → 이더리움 Feature 스케일링 설정 ────────────────
# 각 feature별 (base, scale) 튜플:  result = base + value * scale
#
# base  : Ganache 값이 0이어도 부여되는 최소 기반값 (시간 feature용)
# scale : 곱셈 스케일 팩터
#
# Ganache는 모든 거래가 수초 내 실행되어 시간 feature가 ≈0 이므로,
# base offset으로 실 이더리움 수준의 최소 시간 간격을 부여하고
# scale로 계좌 간 상대적 차이를 증폭한다.
# ── SHAP 기반 균형 스케일링 ──────────────────────────────────
# SHAP 분석 결과:
#   사기↑: Unique Recv From(+3.3), Time Diff(+2.7), Avg min recv(+1.1)
#   정상↑: total transactions(-3.0), Sent tnx(-0.8), Received Tnx(-0.6),
#          min value received(-0.8), avg val received(-0.6)
#
# 전략: 모든 계좌에 동일하게 적용되는 feature(시간, 고유주소)는 스케일을 낮추고,
#       계좌별로 차이가 나는 feature(비율, 건당 금액)는 유지하여
#       사기/정상 구분력을 높인다.
GANACHE_SCALE_FACTORS = {
    # ── 거래 건수 (적당히 — 너무 크면 정상, 너무 작으면 다 사기) ──
    "Sent tnx":                                        (0, 3),
    "Received Tnx":                                    (0, 3),
    "total transactions (including tnx to create contract": (0, 3),
    # ── 고유 주소 수 (0 고정 — 10개 계좌 시뮬레이션에서 모든 계좌가
    #    비슷한 값을 가져 SHAP +3~6으로 전부 사기 방향 밀어버림) ──
    #    실 이더리움 배포 시 (0, 1)로 복원
    "Unique Sent To Addresses":                        (0, 0),
    "Unique Received From Addresses":                  (0, 0),
    # ── 시간 간격 (축소 — Ganache에서 모든 계좌가 비슷한 시간 분포,
    #    SHAP +1.3~2.7로 전부 사기 방향. 스케일 줄여서 영향 최소화) ──
    "Avg min between sent tnx":                        (0, 500),
    "Avg min between received tnx":                    (0, 500),
    "Time Diff between first and last (Mins)":         (0, 1000),
    # ── ETH 총액 ──
    "total Ether sent":                                (0, 5),
    "total ether received":                            (0, 5),
    "total ether balance":                             (0, 5),
    "total ether sent contracts":                      (0, 5),
    # ── ETH 건당 금액 (계좌별 차이 큼 — 구분력 핵심) ──
    "avg val sent":                                    (0, 3),
    "avg val received":                                (0, 2),
    "min val sent":                                    (0, 3),
    "max val sent":                                    (0, 5),
    "min value received":                              (0, 2),
    "max value received":                              (0, 3),
    "min value sent to contract":                      (0, 3),
    "max val sent to contract":                        (0, 5),
    "avg value sent to contract":                      (0, 3),
    # ── ERC20 건수·주소 ──
    "Total ERC20 tnxs":                                (0, 2),
    "ERC20 uniq sent addr":                            (0, 2),
    "ERC20 uniq rec addr":                             (0, 2),
    "ERC20 uniq rec contract addr":                    (0, 2),
    # ── ERC20 시간 (축소 — 시간 feature 동일 이유) ──
    "ERC20 avg time between sent tnx":                 (0, 500),
    "ERC20 avg time between rec tnx":                  (0, 500),
    "ERC20 avg time between contract tnx":             (0, 500),
    # ── ERC20 금액 ──
    "ERC20 total Ether received":                      (0, 3),
    "ERC20 total ether sent":                          (0, 3),
    "ERC20 total Ether sent contract":                 (0, 3),
    "ERC20 min val rec":                               (0, 2),
    "ERC20 max val rec":                               (0, 2),
    "ERC20 avg val rec":                               (0, 2),
    "ERC20 min val sent":                              (0, 2),
    "ERC20 max val sent":                              (0, 2),
    "ERC20 avg val sent":                              (0, 2),
    "ERC20 min val sent contract":                     (0, 2),
    "ERC20 max val sent contract":                     (0, 2),
    "ERC20 avg val sent contract":                     (0, 2),
    # ── 비율·플래그 (변환 불필요, 계좌별 차이 가장 큼) ──
    "sent_received_ratio":                             (0, 1),
    "unique_counterparty_ratio":                       (0, 1),
    "has_erc20_activity":                              (0, 1),
    "Number of Created Contracts":                     (0, 1),
}
