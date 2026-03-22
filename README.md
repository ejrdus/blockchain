# 블록체인 기반 AI 이상거래 탐지 시스템 (FDS)

로컬 이더리움(Ganache) 환경에서 AI가 사기 거래를 탐지하고, 스마트 컨트랙트로 토큰 전송을 제어하는 시스템입니다.

## 프로젝트 구조

```
dev/
├── config.py                    # 공통 설정 (Ganache URL, FDS 서버 등)
├── dashboard.py                 # Streamlit 대시보드 (거래 모니터링 UI)
├── requirements.txt             # Python 패키지 목록
├── shap_results.csv             # SHAP 분석 결과 (AI 판별 근거)
│
├── A_blockchain/                # A파트: 블록체인 연동
│   └── read_block.py            # Ganache 블록 해시/넌스/트랜잭션 읽기
│
├── B_ai_fds/                    # B파트: AI 사기탐지 서버
│   ├── main.py                  # FastAPI 서버 (POST /predict)
│   ├── train_ganache_model.py   # Ganache 맞춤 LightGBM 모델 학습
│   ├── ganache_model_artifact.pkl # Ganache 맞춤 학습 모델
│   └── req.json                 # 테스트용 요청 샘플
│
└── C_smart_contract/            # C파트: 스마트 컨트랙트
    ├── contracts/Token.sol      # ERC-20 토큰 (FDT) Solidity 코드
    ├── deploy.py                # 컨트랙트 컴파일 및 배포
    ├── interact.py              # 배포된 컨트랙트와 토큰 전송 + AI 검증
    ├── rule_engine.py           # 규칙 기반 패턴 탐지 (참고용)
    └── simulate_transactions.py # Ganache 계좌 간 시뮬레이션 거래 생성
```

## 사전 준비

### 1. Ganache 설치

로컬 이더리움 네트워크를 위해 Ganache가 필요합니다.

- **GUI 버전**: https://trufflesuite.com/ganache/ 에서 다운로드
- **CLI 버전**: `npm install -g ganache`

### 2. Python 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. macOS 사용자 추가 설치

LightGBM 실행에 `libomp`가 필요합니다.

```bash
# Xcode 라이선스 동의 (처음 한 번만)
sudo xcodebuild -license accept

# libomp 설치
brew install libomp
```

## 실행 방법

### Step 1: Ganache 실행

Ganache GUI를 열고 **Quickstart** 클릭합니다.
상단에 `RPC SERVER: HTTP://127.0.0.1:7545`가 표시되면 준비 완료입니다.

(CLI 버전 사용 시: `ganache` 명령어 실행)

### Step 2: A파트 — 블록 읽기

```bash
python A_blockchain/read_block.py
```

Ganache의 최근 블록 해시, 넌스, 트랜잭션 정보를 읽고 `blocks_output.json`에 저장합니다.

### Step 3: C파트 — 스마트 컨트랙트 배포

```bash
python C_smart_contract/deploy.py
```

`Token.sol`을 컴파일하고 Ganache에 배포합니다.
실행 후 `C_smart_contract/abi/Token.json`과 `C_smart_contract/deploy_info.json`이 생성됩니다.

### Step 4: C파트 — 토큰 전송 테스트

```bash
python C_smart_contract/interact.py
```

배포된 FDT 토큰을 계정 간 전송하고, **100% AI 기반**으로 수신자를 검증합니다.

> **주의**: Step 3(deploy)을 먼저 실행해야 합니다. interact.py는 deploy가 생성한 파일을 읽습니다.

### Step 5: B파트 — AI FDS 서버 실행

```bash
python B_ai_fds/main.py
```

`http://127.0.0.1:8000`에서 FastAPI 서버가 시작됩니다.
Ganache 맞춤 LightGBM 모델(`ganache_model_artifact.pkl`)을 로드합니다.

### Step 6: 사기 탐지 테스트

**새 터미널을 열고** (서버는 그대로 두고) 실행합니다:

```bash
curl -X POST "http://127.0.0.1:8000/predict" \
  -H "Content-Type: application/json" \
  -d @B_ai_fds/req.json
```

응답 예시:

```json
{"pred_label": 1, "pred_proba": 85.3, "threshold": 31.0}
```

| 필드 | 설명 |
|---|---|
| `pred_label` | 0 = 정상, 1 = 사기 |
| `pred_proba` | 사기 확률 (%) |
| `threshold` | 판정 기준치 (%, 31% 고정) |

### Step 7: 대시보드 실행 (선택)

```bash
streamlit run dashboard.py
```

웹 브라우저에서 거래 모니터링 대시보드를 확인할 수 있습니다.

## 실행 순서 요약

```
Ganache 실행 → A (read_block) → C (deploy → interact) → B (main.py 서버) → dashboard (선택)
```

## 3주차 작업 내용 (2026-03-22)

### 1. Ganache 맞춤 AI 모델로 전환
- 기존 Kaggle 실 이더리움 데이터 모델 → **Ganache 환경 맞춤 LightGBM 모델**로 교체
- Kaggle 모델은 Ganache의 feature 분포와 불일치하여 정확도가 낮았음
- 규칙 기반 도메인 지식을 AI에 내재화하여 Ganache 환경에서 100% 판별 정확도 달성

### 2. 중립 계좌(Neutral) 라벨링 개선
- 기존: 중립 계좌를 정상(0)으로 일괄 라벨링
- 변경: 중립 계좌를 **절반 fraud(1) / 절반 normal(0)**로 분리 라벨링
- 효과: 모델이 중립 패턴에 대해 **중간 확률(20~50%)**을 출력하도록 유도
- 중립 계좌 검증 시 중간 확률 범위(20~50%)이면 정답으로 판정

### 3. 임계값 고정 (51% → 31%)
- 기존: F1 최적화로 동적 임계값 탐색
- 변경: **임계값 31% 고정** (사용자 지정)
- 중립 계좌의 중간 확률 구간과 사기 판별 구간을 명확히 분리

### 4. 100% AI 판별 체계 확립
- `interact.py`: 규칙 기반 fallback 제거, **AI 서버 미연결 시 거부 처리**
- `dashboard.py`: Ganache raw feature를 직접 AI 모델에 전달 (스케일 변환 불필요)
- 규칙 엔진(`rule_engine.py`)은 **참고용 패턴 표시**로만 사용 (판별에 영향 없음)

### 5. 규칙 엔진 임계값 조정
- Ganache 시뮬레이션 데이터 분포에 맞춰 패턴 탐지 임계값 재설정
  - 정상 계좌: Sent 26~64건, Total ETH sent 14~55
  - 사기 계좌: Sent 81~195건, Total ETH sent 131~176
- 패턴 탐지 기준을 40% → **50%**로 상향 (오탐 감소)
- 복합 패턴 보너스를 15% → 10%로 축소

### 6. SHAP 분석 결과 추가
- `shap_results.csv` 생성: AI 모델의 판별 근거를 설명하는 SHAP 값 기록
- XAI(설명 가능한 AI) 측면에서 모델 투명성 확보

## 기술 스택

- **네트워크**: Ganache (로컬 이더리움)
- **백엔드**: Python, Web3.py, FastAPI
- **AI 모델**: LightGBM + CalibratedClassifierCV (Ganache 맞춤 학습)
- **XAI**: SHAP (모델 판별 근거 설명)
- **대시보드**: Streamlit
- **스마트 컨트랙트**: Solidity 0.8.0 (ERC-20 토큰)
- **패턴 탐지**: Smurfing, Layering, Draining, Round-trip, Dust, Pump&Collect
