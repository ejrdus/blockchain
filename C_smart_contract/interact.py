"""
C파트 — 에스크로 기반 토큰 전송 스크립트

흐름:
  1. 송신자가 escrowDeposit() → 토큰을 컨트랙트에 잠금
  2. A파트로 수신자 지갑 분석 → B파트 AI 판별 (100% AI)
  3. 정상 → escrowApprove() : 수신자에게 전송
     사기  → escrowReject()  : 송신자에게 반환

v2: Ganache 맞춤 모델로 100% AI 판별 (규칙 엔진 제거)
"""

import json
import os
import sys

import requests
from web3 import Web3

# 루트 config.py를 import하기 위해 상위 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_URL, FDS_SERVER_URL, FDS_ENDPOINT

# A파트 analyze_address import
sys.path.append(os.path.join(os.path.dirname(__file__), "../A_blockchain"))
from read_block import analyze_address

# 규칙 기반 사기 탐지 엔진 (패턴 이름 참조용으로 유지)
from rule_engine import rule_based_score

BASE_DIR = os.path.dirname(__file__)


def load_contract(w3):
    """배포 정보와 ABI를 읽어 컨트랙트 객체를 반환한다."""
    deploy_info_path = os.path.join(BASE_DIR, "deploy_info.json")
    if not os.path.exists(deploy_info_path):
        print("[!] deploy_info.json이 없습니다. 먼저 deploy.py를 실행하세요.")
        sys.exit(1)

    with open(deploy_info_path, "r") as f:
        deploy_info = json.load(f)

    abi_path = os.path.join(BASE_DIR, "abi", "Token.json")
    with open(abi_path, "r") as f:
        abi = json.load(f)

    contract_address = deploy_info["contract_address"]
    contract = w3.eth.contract(address=contract_address, abi=abi)
    return contract, deploy_info


def get_token_info(contract):
    """토큰 기본 정보를 조회한다."""
    name         = contract.functions.name().call()
    symbol       = contract.functions.symbol().call()
    decimals     = contract.functions.decimals().call()
    total_supply = contract.functions.totalSupply().call()

    print("=" * 55)
    print(f"  토큰 이름   : {name}")
    print(f"  심볼       : {symbol}")
    print(f"  소수점     : {decimals}")
    print(f"  총 발행량  : {total_supply / (10 ** decimals):,.0f} {symbol}")
    print("=" * 55)


def check_balance(contract, address, label=""):
    """특정 주소의 토큰 잔액을 조회한다."""
    decimals = contract.functions.decimals().call()
    symbol   = contract.functions.symbol().call()
    balance  = contract.functions.balanceOf(address).call()
    readable = balance / (10 ** decimals)
    tag = f"[{label}] " if label else ""
    print(f"  {tag}{address[:10]}...  잔액: {readable:,.2f} {symbol}")
    return balance


def check_receiver_history(w3, address) -> dict:
    """
    수신자의 거래 이력을 확인한다.
    이력이 없거나 부족하면 경고 정보를 반환한다.

    반환:
      {
        "has_history": bool,       # 거래 이력 존재 여부
        "sent_count": int,         # 송신 건수
        "recv_count": int,         # 수신 건수
        "warning_level": str,      # "none" | "caution" | "danger"
        "warning_message": str,    # 경고 메시지
      }
    """
    target = w3.to_checksum_address(address)
    sent_count = 0
    recv_count = 0

    latest = w3.eth.block_number
    for num in range(0, latest + 1):
        block = w3.eth.get_block(num, full_transactions=True)
        for tx in block.transactions:
            if tx["from"] == target:
                sent_count += 1
            if tx["to"] == target:
                recv_count += 1

    total = sent_count + recv_count

    if total == 0:
        return {
            "has_history": False,
            "sent_count": 0,
            "recv_count": 0,
            "warning_level": "danger",
            "warning_message": (
                "⚠️  [경고] 수신자의 거래 이력이 전혀 없습니다!\n"
                "     이 주소는 신규 생성되었거나 알 수 없는 지갑입니다.\n"
                "     송금 시 각별한 주의가 필요합니다."
            ),
        }
    elif total <= 2:
        return {
            "has_history": True,
            "sent_count": sent_count,
            "recv_count": recv_count,
            "warning_level": "caution",
            "warning_message": (
                f"⚠️  [주의] 수신자의 거래 이력이 매우 적습니다. "
                f"(송신 {sent_count}건 / 수신 {recv_count}건)\n"
                "     AI 검증 데이터가 부족하여 정확도가 낮을 수 있습니다."
            ),
        }
    else:
        return {
            "has_history": True,
            "sent_count": sent_count,
            "recv_count": recv_count,
            "warning_level": "none",
            "warning_message": "",
        }


def ai_verify(w3, address) -> tuple[bool, float, dict]:
    """
    수신자 이력 확인 → A파트 feature 계산 → B파트 AI 판별 (100% AI).
    반환: (정상 여부, 사기 확률%, 검증 상세 결과 dict)
    """
    result_detail = {
        "receiver": address,
        "history_check": None,
        "features": None,
        "ai_result": None,
        "final_decision": None,
    }

    # ── Step A: 수신자 거래 이력 확인 ──
    print(f"\n  [🔍 이력 확인] {address[:10]}... 수신자 거래 이력 조회 중...")
    history = check_receiver_history(w3, address)
    result_detail["history_check"] = history

    if history["warning_level"] != "none":
        print(f"\n  {history['warning_message']}")

    if history["warning_level"] == "danger":
        # 이력 전무 → AI 검증 불가, 경고와 함께 보류 처리
        print("  → 거래 이력 없음: AI 검증 불가. 송신자 확인 필요.")
        result_detail["final_decision"] = "hold_no_history"
        return False, -1.0, result_detail

    # ── Step B: A파트 feature 추출 ──
    print(f"\n  [🤖 AI 검증] {address[:10]}... 지갑 분석 중...")
    features = analyze_address(w3, address)
    result_detail["features"] = features

    # ── Step C: B파트 AI 판별 (100% AI) ──
    ai_proba = -1.0
    try:
        res = requests.post(
            FDS_SERVER_URL + FDS_ENDPOINT,
            json={"features": features},
            timeout=10
        )
        ai_result = res.json()
        ai_proba = ai_result["pred_proba"]
        result_detail["ai_result"] = ai_result
        print(f"  [AI 모델] 사기 확률: {ai_proba}%")
    except requests.exceptions.ConnectionError:
        print("  [!] B파트 FDS 서버 미연결 — AI 판별 불가")

    # ── Step D: 패턴 참조 (보조 정보) ──
    rule_info = rule_based_score(features)
    result_detail["rule_info"] = rule_info

    if rule_info["detected_patterns"]:
        print(f"  [참고 패턴] {', '.join(rule_info['detected_patterns'])}")

    # ── Step E: 최종 판별 (100% AI) ──
    if ai_proba < 0:
        # AI 서버 미연결 → 규칙 기반 fallback
        final_score = rule_info["rule_score"]
        print(f"  [Fallback] 규칙 기반 점수: {final_score}%")
        is_fraud = final_score >= 40
    else:
        final_score = ai_proba
        threshold_pct = ai_result.get("threshold", 50)
        is_fraud = ai_proba >= threshold_pct
        print(f"  [최종 점수] {final_score}% (AI 100%, 임계값 {threshold_pct}%)")

    if history["warning_level"] == "caution":
        print("  ℹ️  이력 부족으로 정확도가 낮을 수 있음")

    is_safe = not is_fraud
    result_detail["final_score"] = final_score
    result_detail["final_decision"] = "approved" if is_safe else "rejected"
    return is_safe, final_score, result_detail


def escrow_send(w3, contract, sender, receiver, amount):
    """
    에스크로 전체 흐름 실행:
      1) 수신자 이력 사전 확인 (이력 없으면 경고)
      2) deposit → 토큰 잠금
      3) AI 검증 (이력 있을 때만)
      4) approve or reject or hold

    반환: 거래 결과 dict
    """
    decimals   = contract.functions.decimals().call()
    symbol     = contract.functions.symbol().call()
    raw_amount = int(amount * (10 ** decimals))

    print(f"\n{'─'*55}")
    print(f"  송신자 : {sender[:10]}...")
    print(f"  수신자 : {receiver[:10]}...")
    print(f"  금액   : {amount:,.2f} {symbol}")

    # ── Step 1: 토큰을 컨트랙트에 예치 ──
    print("\n  [1단계] 토큰을 컨트랙트에 예치(잠금)...")
    tx = contract.functions.escrowDeposit(receiver, raw_amount).build_transaction({
        "from":     sender,
        "nonce":    w3.eth.get_transaction_count(sender),
        "gas":      200_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    logs   = contract.events.EscrowDeposited().process_receipt(receipt)
    tx_id  = logs[0]["args"]["txId"]
    print(f"  ✅ 예치 완료 | EscrowTxId: {tx_id} | Block: {receipt.blockNumber}")

    # ── Step 2: AI로 수신자 검증 (이력 확인 포함) ──
    print("\n  [2단계] 수신자 검증 중...")
    is_safe, proba, detail = ai_verify(w3, receiver)

    # ── Step 3: 결과에 따라 승인 or 거부 or 보류 ──
    deployer = w3.eth.accounts[0]
    final_score = detail.get("final_score", proba)

    if detail["final_decision"] == "hold_no_history":
        # 수신자 이력 없음 → 거부 처리 + 송신자에게 반환
        print(f"\n  [3단계] 수신자 이력 없음 → 송신자에게 반환 (경고)")
        print(f"  ┌{'─'*50}┐")
        print(f"  │ ⚠️  경고: 수신자({receiver[:10]}...)의 거래       │")
        print(f"  │ 이력이 전혀 없습니다.                          │")
        print(f"  │ AI 사기 검증을 수행할 수 없어 안전을 위해      │")
        print(f"  │ 송금이 보류(반환) 처리되었습니다.               │")
        print(f"  │                                                │")
        print(f"  │ 수신자에게 거래 이력 확보 후 재시도하세요.      │")
        print(f"  └{'─'*50}┘")

        tx = contract.functions.escrowReject(tx_id).build_transaction({
            "from":     deployer,
            "nonce":    w3.eth.get_transaction_count(deployer),
            "gas":      100_000,
            "gasPrice": w3.eth.gas_price,
        })
        tx_hash = w3.eth.send_transaction(tx)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"  ↩️  {amount:,.2f} {symbol} → 송신자에게 반환 완료")

    elif is_safe:
        # 임계값 근처인지 확인 (caution zone)
        threshold_pct = detail.get("ai_result", {}).get("threshold", 50) if detail.get("ai_result") else 40
        caution_lower = threshold_pct * 0.6   # 임계값의 60% 이상이면 주의
        is_caution = final_score >= caution_lower

        if is_caution:
            print(f"\n  [3단계] 정상 판별이나 주의 필요 → 전송 승인 (경고 포함)")
            print(f"  ┌{'─'*50}┐")
            print(f"  │ ⚠️  주의: 사기 확률({final_score:.1f}%)이 임계값     │")
            print(f"  │ ({threshold_pct}%)에 근접합니다.                     │")
            print(f"  │                                                │")
            print(f"  │ 수신자의 거래 패턴에 일부 의심스러운 요소가     │")
            print(f"  │ 감지되었습니다. 거래 상대방을 한 번 더         │")
            print(f"  │ 확인하시기 바랍니다.                           │")
            print(f"  └{'─'*50}┘")
        else:
            print(f"\n  [3단계] 정상 판별 → 수신자에게 전송 승인")

        tx = contract.functions.escrowApprove(tx_id).build_transaction({
            "from":     deployer,
            "nonce":    w3.eth.get_transaction_count(deployer),
            "gas":      100_000,
            "gasPrice": w3.eth.gas_price,
        })
        tx_hash = w3.eth.send_transaction(tx)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"  ✅ 전송 완료! {amount:,.2f} {symbol} → {receiver[:10]}...")

    else:
        # 임계값 바로 위인지 확인 (caution zone)
        threshold_pct = detail.get("ai_result", {}).get("threshold", 50) if detail.get("ai_result") else 40
        caution_upper = threshold_pct * 1.4   # 임계값의 140% 이하면 주의
        is_caution = final_score <= caution_upper

        if is_caution:
            print(f"\n  [3단계] 사기 의심(확률 {proba:.1f}%) → 송신자에게 반환")
            print(f"  ┌{'─'*50}┐")
            print(f"  │ ⚠️  참고: 사기 확률({final_score:.1f}%)이 임계값     │")
            print(f"  │ ({threshold_pct}%) 근처입니다.                      │")
            print(f"  │                                                │")
            print(f"  │ 확실한 사기가 아닐 수 있으니 수신자 정보를     │")
            print(f"  │ 직접 확인한 후 재시도를 고려하세요.            │")
            print(f"  └{'─'*50}┘")
        else:
            print(f"\n  [3단계] 사기 의심(확률 {proba:.1f}%) → 송신자에게 반환")

        tx = contract.functions.escrowReject(tx_id).build_transaction({
            "from":     deployer,
            "nonce":    w3.eth.get_transaction_count(deployer),
            "gas":      100_000,
            "gasPrice": w3.eth.gas_price,
        })
        tx_hash = w3.eth.send_transaction(tx)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"  ⚠️  전송 취소! {amount:,.2f} {symbol} → 송신자에게 반환 완료")

    print(f"{'─'*55}")
    return detail


def main():
    # ── Ganache 연결 ──
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    if not w3.is_connected():
        print("[!] Ganache에 연결할 수 없습니다.")
        sys.exit(1)
    print(f"[+] Ganache 연결 성공 — {GANACHE_URL}\n")

    # ── 컨트랙트 로드 ──
    contract, deploy_info = load_contract(w3)
    print(f"[+] 컨트랙트 로드 완료 — {deploy_info['contract_address']}\n")

    get_token_info(contract)

    accounts  = w3.eth.accounts
    deployer  = accounts[0]
    receiver1 = accounts[1]
    receiver2 = accounts[2]

    # ── 전송 전 잔액 ──
    print("\n[잔액 확인 — 전송 전]")
    check_balance(contract, deployer,          "배포자")
    check_balance(contract, receiver1,         "수신자1")
    check_balance(contract, receiver2,         "수신자2")
    check_balance(contract, contract.address,  "컨트랙트(에스크로)")

    # ── 에스크로 전송 ──
    print("\n\n=== 에스크로 전송 시작 ===")
    escrow_send(w3, contract, deployer, receiver1, 500)
    escrow_send(w3, contract, deployer, receiver2, 300)

    # ── 전송 후 잔액 ──
    print("\n[잔액 확인 — 전송 후]")
    check_balance(contract, deployer,          "배포자")
    check_balance(contract, receiver1,         "수신자1")
    check_balance(contract, receiver2,         "수신자2")
    check_balance(contract, contract.address,  "컨트랙트(에스크로)")

    print("\n[+] interact.py 실행 완료")


if __name__ == "__main__":
    main()
