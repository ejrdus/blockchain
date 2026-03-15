# 블록체인 기반 AI 이상거래 탐지 시스템 (FDS)

로컬 이더리움(Ganache) 환경에서 AI가 사기 거래를 탐지하고, 스마트 컨트랙트로 토큰 전송을 제어하는 시스템입니다.

## 프로젝트 구조

```
dev/
├── config.py                    # 공통 설정 (Ganache URL, FDS 서버 등)
├── requirements.txt             # Python 패키지 목록
│
├── A_blockchain/                # A파트: 블록체인 연동
│   ├── read_block.py            # Ganache 블록 해시/넌스/트랜잭션 읽기
│   └── ganache_setup.md         # Ganache 실행 가이드
│
├── B_ai_fds/                    # B파트: AI 사기탐지 서버
│   ├── main.py                  # FastAPI 서버 (POST /predict)
│   ├── fraud_model_artifact.pkl # 학습된 LightGBM 모델
│   ├── req.json                 # 테스트용 요청 샘플
│   └── train/                   # 모델 학습 자료
│       ├── transaction_dataset.csv  # Kaggle 원본 데이터셋
│       ├── pre.ipynb                # 전처리 노트북
│       ├── dataset.csv              # 전처리 완료 데이터
│       └── train.ipynb              # 모델 학습 노트북
│
└── C_smart_contract/            # C파트: 스마트 컨트랙트
    ├── contracts/Token.sol      # ERC-20 토큰 (FDT) + FraudAudit 컨트랙트
    ├── deploy.py                # Token + FraudAudit 컴파일 및 배포
    └── interact.py              # FDS 연동 토큰 전송 (사기 탐지 → 차단/허용)
```

## 시스템 흐름 (2주차 완성)

```
송금 시도
  │
  ▼
interact.py — 거래 Feature 구성
  │
  ▼
FDS 서버 (main.py) — POST /predict
  │
  ├─ 정상 (사기 확률 < 임계값) → 토큰 전송 실행
  │
  └─ 사기 (사기 확률 ≥ 임계값) → 거래 차단
  │
  ▼
FraudAudit 컨트랙트 — 위험도 점수 해시를 블록에 기록 (ZKP 간략화)
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

### Step 2: FDS 서버 실행

```bash
python B_ai_fds/main.py
```

`http://127.0.0.1:8000`에서 FastAPI 서버가 시작됩니다.

### Step 3: 스마트 컨트랙트 배포

**새 터미널**에서 실행합니다:

```bash
python C_smart_contract/deploy.py
```

Token + FraudAudit 컨트랙트를 컴파일하고 Ganache에 배포합니다.

### Step 4: 데모 실행 (FDS 연동 토큰 전송)

```bash
python C_smart_contract/interact.py
```

데모 시나리오 2개가 자동 실행됩니다:

1. **정상 거래**: FDS가 정상 판정 → 토큰 전송 완료 → 감사 기록 (통과)
2. **사기 거래**: FDS가 사기 탐지 → 토큰 전송 차단 → 감사 기록 (차단)

두 경우 모두 AI 위험도 점수가 해시화되어 블록체인에 기록됩니다.

### (선택) 블록 읽기

```bash
python A_blockchain/read_block.py
```

Ganache의 최근 블록 해시, 넌스, 트랜잭션 정보를 확인합니다.

## 실행 순서 요약

```
Ganache 실행 → B (main.py 서버) → C (deploy → interact) → A (read_block, 선택)
```

## 기술 스택

- **네트워크**: Ganache (로컬 이더리움)
- **백엔드**: Python, Web3.py, FastAPI
- **AI 모델**: LightGBM (Kaggle Ethereum Fraud Detection 데이터셋)
- **스마트 컨트랙트**: Solidity 0.8.0 (ERC-20 토큰 + FraudAudit)
- **ZKP(간략화)**: keccak256 해시 기반 위험도 점수 블록 기록
