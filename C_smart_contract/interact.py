"""
C파트 — 스마트 컨트랙트 호출 / 토큰 전송 스크립트
deploy.py로 배포된 Token + FraudAudit 컨트랙트와 상호작용한다.

[2주차] FDS 서버 연동:
  송금 시도 → FDS 서버에 거래 특성값 전송 → 사기 확률 리턴
  → 정상이면 토큰 전송 / 사기면 차단
  → AI 위험도 점수를 해시화하여 FraudAudit 컨트랙트에 기록 (ZKP 간략화)
"""

import hashlib
import json
import os
import sys
import time

import requests
from web3 import Web3

# 루트 config.py를 import하기 위해 상위 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_URL, FDS_SERVER_URL, FDS_ENDPOINT

BASE_DIR = os.path.dirname(__file__)

# ── FDS 서버 임계값 (이 확률 이상이면 사기로 차단) ──
# FDS 서버가 리턴하는 threshold를 사용하되, 연결 실패 시 기본값
DEFAULT_THRESHOLD = 32.0  # percent


# ═══════════════════════════════════════════════════════════════
#  컨트랙트 로딩
# ═══════════════════════════════════════════════════════════════
def load_contracts(w3):
    """배포 정보와 ABI를 읽어 Token, FraudAudit 컨트랙트 객체를 반환한다."""
    deploy_info_path = os.path.join(BASE_DIR, "deploy_info.json")
    if not os.path.exists(deploy_info_path):
        print("[!] deploy_info.json이 없습니다. 먼저 deploy.py를 실행하세요.")
        sys.exit(1)

    with open(deploy_info_path, "r") as f:
        deploy_info = json.load(f)

    # Token ABI & 컨트랙트
    token_abi_path = os.path.join(BASE_DIR, "abi", "Token.json")
    with open(token_abi_path, "r") as f:
        token_abi = json.load(f)

    token_address = deploy_info.get("token_address", deploy_info.get("contract_address"))
    token_contract = w3.eth.contract(address=token_address, abi=token_abi)

    # FraudAudit ABI & 컨트랙트 (2주차)
    audit_abi_path = os.path.join(BASE_DIR, "abi", "FraudAudit.json")
    if os.path.exists(audit_abi_path) and "audit_address" in deploy_info:
        with open(audit_abi_path, "r") as f:
            audit_abi = json.load(f)
        audit_contract = w3.eth.contract(address=deploy_info["audit_address"], abi=audit_abi)
    else:
        audit_contract = None
        print("[!] FraudAudit 컨트랙트를 찾을 수 없습니다. deploy.py를 다시 실행하세요.")

    return token_contract, audit_contract, deploy_info


# ═══════════════════════════════════════════════════════════════
#  토큰 정보 / 잔액 조회
# ═══════════════════════════════════════════════════════════════
def get_token_info(contract):
    """토큰 기본 정보를 조회한다."""
    name = contract.functions.name().call()
    symbol = contract.functions.symbol().call()
    decimals = contract.functions.decimals().call()
    total_supply = contract.functions.totalSupply().call()

    print("=" * 60)
    print(f"  토큰 이름   : {name}")
    print(f"  심볼       : {symbol}")
    print(f"  소수점     : {decimals}")
    print(f"  총 발행량  : {total_supply / (10 ** decimals):,.0f} {symbol}")
    print("=" * 60)


def check_balance(contract, address):
    """특정 주소의 토큰 잔액을 조회한다."""
    decimals = contract.functions.decimals().call()
    symbol = contract.functions.symbol().call()
    balance = contract.functions.balanceOf(address).call()
    readable = balance / (10 ** decimals)
    print(f"  [{address[:10]}...] 잔액: {readable:,.2f} {symbol}")
    return balance


# ═══════════════════════════════════════════════════════════════
#  [2주차] FDS 서버 연동
# ═══════════════════════════════════════════════════════════════
def build_features(w3, sender, receiver, amount, tx_history=None):
    """
    송금 시도에 대한 Feature Dict를 구성한다.
    실제 운영 환경에서는 블록체인 상의 과거 거래 이력을 분석하여
    각 피처를 계산하지만, 데모 환경에서는 기본값 + 시뮬레이션 값을 사용한다.

    tx_history가 제공되면 해당 값들을 사용한다.
    """
    if tx_history:
        return tx_history

    # 데모용 기본 피처 (Ganache 로컬 환경)
    return {
        "Avg min between sent tnx": 0,
        "Avg min between received tnx": 0,
        "Time Diff between first and last (Mins)": 0,
        "Sent tnx": 0,
        "Received Tnx": 0,
        "Number of Created Contracts": 0,
        "Unique Received From Addresses": 0,
        "Unique Sent To Addresses": 0,
        "min value received": 0,
        "max value received": 0,
        "avg val received": 0,
        "min val sent": 0,
        "max val sent": 0,
        "avg val sent": 0,
        "min value sent to contract": 0,
        "max val sent to contract": 0,
        "avg value sent to contract": 0,
        "total transactions (including tnx to create contract": 0,
        "total Ether sent": 0,
        "total ether received": 0,
        "total ether sent contracts": 0,
        "total ether balance": 0,
        "Total ERC20 tnxs": 0,
        "ERC20 total Ether received": 0,
        "ERC20 total ether sent": 0,
        "ERC20 total Ether sent contract": 0,
        "ERC20 uniq sent addr": 0,
        "ERC20 uniq rec addr": 0,
        "ERC20 uniq rec contract addr": 0,
        "ERC20 avg time between sent tnx": 0,
        "ERC20 avg time between rec tnx": 0,
        "ERC20 avg time between contract tnx": 0,
        "ERC20 min val rec": 0,
        "ERC20 max val rec": 0,
        "ERC20 avg val rec": 0,
        "ERC20 min val sent": 0,
        "ERC20 max val sent": 0,
        "ERC20 avg val sent": 0,
        "ERC20 min val sent contract": 0,
        "ERC20 max val sent contract": 0,
        "ERC20 avg val sent contract": 0,
        "has_erc20_activity": 0,
        "sent_received_ratio": 0,
        "unique_counterparty_ratio": 0,
    }


def query_fds(features):
    """
    FDS 서버에 거래 특성값을 전송하여 사기 확률을 리턴받는다.
    POST /predict → {"pred_label": 0/1, "pred_proba": float, "threshold": float}
    """
    url = f"{FDS_SERVER_URL}{FDS_ENDPOINT}"
    payload = {"features": features}

    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        result = resp.json()
        print(f"\n  [FDS] 서버 응답:")
        print(f"    사기 확률   : {result['pred_proba']:.2f}%")
        print(f"    임계값      : {result['threshold']}%")
        print(f"    판정 라벨   : {'🚨 사기' if result['pred_label'] == 1 else '✅ 정상'}")
        return result
    except requests.exceptions.ConnectionError:
        print(f"\n  [FDS] ⚠️  FDS 서버에 연결할 수 없습니다 ({url})")
        print(f"         B_ai_fds/main.py를 먼저 실행하세요!")
        print(f"         → 연결 실패 시 거래를 차단합니다 (안전 모드)")
        return None
    except Exception as e:
        print(f"\n  [FDS] ⚠️  FDS 서버 오류: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  [2주차] ZKP 간략화 — 위험도 점수 해시 생성 및 블록 기록
# ═══════════════════════════════════════════════════════════════
def compute_score_hash(sender, receiver, amount, fraud_score, timestamp):
    """
    AI 위험도 점수 + 거래 정보를 keccak256으로 해시화한다.
    이 해시를 블록체인에 기록하여 데이터 불변성을 보장한다 (ZKP 간략화).
    """
    # 해시 입력: 송신자 + 수신자 + 금액 + 사기확률 + 타임스탬프
    raw = f"{sender}|{receiver}|{amount}|{fraud_score}|{timestamp}"
    hash_bytes = Web3.solidity_keccak(["string"], [raw])
    return hash_bytes


def record_audit_on_chain(w3, audit_contract, deployer, sender, receiver, amount, fraud_score, score_hash, blocked):
    """
    FraudAudit 컨트랙트에 감사 기록을 남긴다.
    """
    # fraud_score를 정수로 변환 (예: 87.32% → 8732)
    score_int = int(fraud_score * 100)
    # amount를 wei 단위로 변환
    amount_wei = Web3.to_wei(amount, "ether")

    tx = audit_contract.functions.recordAudit(
        sender, receiver, amount_wei, score_int, score_hash, blocked
    ).build_transaction({
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 300_000,
        "gasPrice": w3.eth.gas_price,
    })

    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    record_count = audit_contract.functions.getAuditCount().call()
    print(f"  [Audit] 블록체인 기록 완료!")
    print(f"    기록 ID    : {record_count - 1}")
    print(f"    해시       : {score_hash.hex()}")
    print(f"    차단 여부  : {'차단됨' if blocked else '통과'}")
    print(f"    TX 해시    : {tx_hash.hex()}")
    print(f"    블록 번호  : {receipt.blockNumber}")

    return receipt


# ═══════════════════════════════════════════════════════════════
#  [2주차] 통합: FDS 검증 + 토큰 전송 + 감사 기록
# ═══════════════════════════════════════════════════════════════
def safe_transfer(w3, token_contract, audit_contract, deployer, sender, receiver, amount, tx_history=None):
    """
    FDS 서버와 연동된 안전한 토큰 전송.
    1) 거래 특성값(feature) 구성
    2) FDS 서버에 전송하여 사기 확률 조회
    3) 사기 → 차단 / 정상 → 토큰 전송
    4) AI 위험도 점수를 해시화하여 블록에 기록 (ZKP 간략화)
    """
    decimals = token_contract.functions.decimals().call()
    symbol = token_contract.functions.symbol().call()

    print(f"\n{'='*60}")
    print(f"  📤 송금 시도: {sender[:10]}... → {receiver[:10]}...")
    print(f"     금액: {amount:,.2f} {symbol}")
    print(f"{'='*60}")

    # ── Step 1: Feature 구성 ──
    features = build_features(w3, sender, receiver, amount, tx_history)

    # ── Step 2: FDS 서버에 질의 ──
    fds_result = query_fds(features)

    if fds_result is None:
        # FDS 서버 연결 실패 → 안전 모드: 차단
        fraud_score = 100.0
        is_fraud = True
        print(f"\n  ⛔ FDS 서버 미응답 — 안전 모드로 거래를 차단합니다.")
    else:
        fraud_score = fds_result["pred_proba"]
        is_fraud = fds_result["pred_label"] == 1

    # ── Step 3: 차단 또는 전송 ──
    timestamp = int(time.time())
    score_hash = compute_score_hash(sender, receiver, amount, fraud_score, timestamp)

    if is_fraud:
        print(f"\n  🚨 사기 의심 거래 차단!")
        print(f"     사기 확률: {fraud_score:.2f}%")
        print(f"     → 토큰 전송이 실행되지 않습니다.")

        # 차단된 거래도 감사 기록은 남긴다 (불변성)
        if audit_contract:
            record_audit_on_chain(
                w3, audit_contract, deployer,
                sender, receiver, amount,
                fraud_score, score_hash, blocked=True
            )
        return None

    else:
        print(f"\n  ✅ 정상 거래 승인!")
        print(f"     사기 확률: {fraud_score:.2f}%")

        # 토큰 전송 실행
        raw_amount = int(amount * (10 ** decimals))
        tx = token_contract.functions.transfer(receiver, raw_amount).build_transaction({
            "from": sender,
            "nonce": w3.eth.get_transaction_count(sender),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
        })

        tx_hash = w3.eth.send_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        print(f"\n  [+] 토큰 전송 완료!")
        print(f"      TX 해시   : {tx_hash.hex()}")
        print(f"      블록 번호 : {receipt.blockNumber}")

        # 정상 거래도 감사 기록을 남긴다
        if audit_contract:
            record_audit_on_chain(
                w3, audit_contract, deployer,
                sender, receiver, amount,
                fraud_score, score_hash, blocked=False
            )
        return receipt


# ═══════════════════════════════════════════════════════════════
#  메인 실행
# ═══════════════════════════════════════════════════════════════
def main():
    # ── Ganache 연결 ──
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))

    if not w3.is_connected():
        print("[!] Ganache에 연결할 수 없습니다.")
        sys.exit(1)

    print(f"[+] Ganache 연결 성공 — {GANACHE_URL}\n")

    # ── 컨트랙트 로드 ──
    token_contract, audit_contract, deploy_info = load_contracts(w3)
    print(f"[+] Token 컨트랙트 로드 완료 — {deploy_info.get('token_address', deploy_info.get('contract_address'))}")
    if audit_contract:
        print(f"[+] FraudAudit 컨트랙트 로드 완료 — {deploy_info['audit_address']}")

    # ── 토큰 정보 조회 ──
    get_token_info(token_contract)

    # ── Ganache 계정 목록 ──
    accounts = w3.eth.accounts
    deployer = accounts[0]
    receiver1 = accounts[1]
    receiver2 = accounts[2]

    # ── 전송 전 잔액 확인 ──
    print("\n[잔액 확인 — 전송 전]")
    check_balance(token_contract, deployer)
    check_balance(token_contract, receiver1)
    check_balance(token_contract, receiver2)

    # ══════════════════════════════════════════════════════════
    # 데모 시나리오 1: 정상 거래
    # 정상적인 거래 패턴의 Feature를 FDS 서버에 전송
    # ══════════════════════════════════════════════════════════
    normal_features = {
        "Avg min between sent tnx": 844.27,
        "Avg min between received tnx": 1200.50,
        "Time Diff between first and last (Mins)": 200000,
        "Sent tnx": 50,
        "Received Tnx": 45,
        "Number of Created Contracts": 0,
        "Unique Received From Addresses": 20,
        "Unique Sent To Addresses": 15,
        "min value received": 0.01,
        "max value received": 5.0,
        "avg val received": 1.2,
        "min val sent": 0.05,
        "max val sent": 3.0,
        "avg val sent": 0.8,
        "min value sent to contract": 0,
        "max val sent to contract": 0,
        "avg value sent to contract": 0,
        "total transactions (including tnx to create contract": 95,
        "total Ether sent": 40.0,
        "total ether received": 54.0,
        "total ether sent contracts": 0,
        "total ether balance": 14.0,
        "Total ERC20 tnxs": 10,
        "ERC20 total Ether received": 5.0,
        "ERC20 total ether sent": 3.0,
        "ERC20 total Ether sent contract": 0,
        "ERC20 uniq sent addr": 5,
        "ERC20 uniq rec addr": 4,
        "ERC20 uniq rec contract addr": 0,
        "ERC20 avg time between sent tnx": 5000,
        "ERC20 avg time between rec tnx": 6000,
        "ERC20 avg time between contract tnx": 0,
        "ERC20 min val rec": 0.1,
        "ERC20 max val rec": 2.0,
        "ERC20 avg val rec": 0.8,
        "ERC20 min val sent": 0.05,
        "ERC20 max val sent": 1.5,
        "ERC20 avg val sent": 0.6,
        "ERC20 min val sent contract": 0,
        "ERC20 max val sent contract": 0,
        "ERC20 avg val sent contract": 0,
        "has_erc20_activity": 1,
        "sent_received_ratio": 0.74,
        "unique_counterparty_ratio": 0.3,
    }

    print("\n" + "▶" * 30)
    print("  데모 시나리오 1: 정상 거래")
    print("▶" * 30)
    safe_transfer(
        w3, token_contract, audit_contract, deployer,
        sender=deployer, receiver=receiver1, amount=500,
        tx_history=normal_features
    )

    # ══════════════════════════════════════════════════════════
    # 데모 시나리오 2: 사기 의심 거래
    # 사기 패턴의 Feature를 FDS 서버에 전송
    # (짧은 시간 내 반복 거래, 수신만 있고 송신 없음 등)
    # ══════════════════════════════════════════════════════════
    fraud_features = {
        "Avg min between sent tnx": 0,
        "Avg min between received tnx": 36572.61,
        "Time Diff between first and last (Mins)": 182863.07,
        "Sent tnx": 0,
        "Received Tnx": 5,
        "Number of Created Contracts": 0,
        "Unique Received From Addresses": 3,
        "Unique Sent To Addresses": 0,
        "min value received": 0,
        "max value received": 0,
        "avg val received": 0,
        "min val sent": 0,
        "max val sent": 0,
        "avg val sent": 0,
        "min value sent to contract": 0,
        "max val sent to contract": 0,
        "avg value sent to contract": 0,
        "total transactions (including tnx to create contract": 6,
        "total Ether sent": 0,
        "total ether received": 0,
        "total ether sent contracts": 0,
        "total ether balance": 0,
        "Total ERC20 tnxs": 0,
        "ERC20 total Ether received": 0,
        "ERC20 total ether sent": 0,
        "ERC20 total Ether sent contract": 0,
        "ERC20 uniq sent addr": 0,
        "ERC20 uniq rec addr": 0,
        "ERC20 uniq rec contract addr": 0,
        "ERC20 avg time between sent tnx": 0,
        "ERC20 avg time between rec tnx": 0,
        "ERC20 avg time between contract tnx": 0,
        "ERC20 min val rec": 0,
        "ERC20 max val rec": 0,
        "ERC20 avg val rec": 0,
        "ERC20 min val sent": 0,
        "ERC20 max val sent": 0,
        "ERC20 avg val sent": 0,
        "ERC20 min val sent contract": 0,
        "ERC20 max val sent contract": 0,
        "ERC20 avg val sent contract": 0,
        "has_erc20_activity": 0,
        "sent_received_ratio": 0,
        "unique_counterparty_ratio": 0,
    }

    print("\n" + "▶" * 30)
    print("  데모 시나리오 2: 사기 의심 거래")
    print("▶" * 30)
    safe_transfer(
        w3, token_contract, audit_contract, deployer,
        sender=deployer, receiver=receiver2, amount=300,
        tx_history=fraud_features
    )

    # ── 전송 후 잔액 확인 ──
    print("\n\n[잔액 확인 — 전송 후]")
    check_balance(token_contract, deployer)
    check_balance(token_contract, receiver1)
    check_balance(token_contract, receiver2)

    # ── 감사 기록 확인 ──
    if audit_contract:
        count = audit_contract.functions.getAuditCount().call()
        print(f"\n[FraudAudit] 총 감사 기록 수: {count}건")
        for i in range(count):
            record = audit_contract.functions.auditLog(i).call()
            print(f"\n  기록 #{i}:")
            print(f"    송신자     : {record[0][:10]}...")
            print(f"    수신자     : {record[1][:10]}...")
            print(f"    금액(wei)  : {record[2]}")
            print(f"    위험도     : {record[3] / 100:.2f}%")
            print(f"    해시       : {record[4].hex()}")
            print(f"    차단 여부  : {'차단' if record[5] else '통과'}")
            print(f"    타임스탬프 : {record[6]}")

    print(f"\n[+] interact.py 실행 완료")


if __name__ == "__main__":
    main()
