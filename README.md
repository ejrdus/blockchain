# 블록체인 기반 AI 이상거래 탐지 시스템 (FDS)

로컬 이더리움(Ganache) 환경에서 AI가 사기 거래를 탐지하고, 스마트 컨트랙트로 토큰 전송을 제어하는 시스템입니다.

## 프로젝트 구조

```
dev/
├── config.py                    # 공통 설정 (Ganache URL, FDS 서버 등)
├── requirements.txt             # Python 패키지 목록
│
├── A_blockchain/                # A파트: 블록체인 연동
│   └── read_block.py            # Ganache 블록 해시/넌스/트랜잭션 읽기
│
├── B_ai_fds/                    # B파트: AI 사기탐지 서버
│   ├── main.py                  # FastAPI 서버 (POST /predict)
│   ├── fraud_model_artifact.pkl # 학습된 LightGBM 모델
│   └── req.json                 # 테스트용 요청 샘플
│
└── C_smart_contract/            # C파트: 스마트 컨트랙트
    ├── contracts/Token.sol      # ERC-20 토큰 (FDT) Solidity 코드
    ├── deploy.py                # 컨트랙트 컴파일 및 배포
    └── interact.py              # 배포된 컨트랙트와 토큰 전송 테스트
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

(CLI 버전 사용 시: `ganache -p 7545` 명령어 실행)

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

배포된 FDT 토큰을 계정 간 전송하고 잔액 변화를 확인합니다.

> **주의**: Step 3(deploy)을 먼저 실행해야 합니다. interact.py는 deploy가 생성한 파일을 읽습니다.

### Step 5: B파트 — AI FDS 서버 실행

```bash
python B_ai_fds/main.py
```

`http://127.0.0.1:8000`에서 FastAPI 서버가 시작됩니다.

### Step 6: 사기 탐지 테스트

**새 터미널을 열고** (서버는 그대로 두고) 실행합니다:

```bash
curl -X POST "http://127.0.0.1:8000/predict" \
  -H "Content-Type: application/json" \
  -d @B_ai_fds/req.json
```

응답 예시:

```json
{"pred_label": 1, "pred_proba": 99.977, "threshold": 32.0}
```

| 필드 | 설명 |
|---|---|
| `pred_label` | 0 = 정상, 1 = 사기 |
| `pred_proba` | 사기 확률 (%) |
| `threshold` | 판정 기준치 (%) |

## 실행 순서 요약

```
Ganache 실행 → A (read_block) → C (deploy → interact) → B (main.py 서버 실행 → curl 테스트)
```

## 기술 스택

- **네트워크**: Ganache (로컬 이더리움)
- **백엔드**: Python, Web3.py, FastAPI
- **AI 모델**: LightGBM (Kaggle Ethereum Fraud Detection 데이터셋)
- **스마트 컨트랙트**: Solidity 0.8.0 (ERC-20 토큰)
