"""
C파트 — 스마트 컨트랙트 호출 / 토큰 전송 스크립트
deploy.py로 배포된 Token 컨트랙트와 상호작용한다.
"""

import json
import os
import sys

from web3 import Web3

# 루트 config.py를 import하기 위해 상위 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_URL

BASE_DIR = os.path.dirname(__file__)


def load_contract(w3):
    """배포 정보와 ABI를 읽어 컨트랙트 객체를 반환한다."""
    # 배포 정보 로드
    deploy_info_path = os.path.join(BASE_DIR, "deploy_info.json")
    if not os.path.exists(deploy_info_path):
        print("[!] deploy_info.json이 없습니다. 먼저 deploy.py를 실행하세요.")
        sys.exit(1)

    with open(deploy_info_path, "r") as f:
        deploy_info = json.load(f)

    # ABI 로드
    abi_path = os.path.join(BASE_DIR, "abi", "Token.json")
    with open(abi_path, "r") as f:
        abi = json.load(f)

    contract_address = deploy_info["contract_address"]
    contract = w3.eth.contract(address=contract_address, abi=abi)
    return contract, deploy_info


def get_token_info(contract):
    """토큰 기본 정보를 조회한다."""
    name = contract.functions.name().call()
    symbol = contract.functions.symbol().call()
    decimals = contract.functions.decimals().call()
    total_supply = contract.functions.totalSupply().call()

    print("=" * 50)
    print(f"  토큰 이름   : {name}")
    print(f"  심볼       : {symbol}")
    print(f"  소수점     : {decimals}")
    print(f"  총 발행량  : {total_supply / (10 ** decimals):,.0f} {symbol}")
    print("=" * 50)


def check_balance(contract, address):
    """특정 주소의 토큰 잔액을 조회한다."""
    decimals = contract.functions.decimals().call()
    symbol = contract.functions.symbol().call()
    balance = contract.functions.balanceOf(address).call()
    readable = balance / (10 ** decimals)
    print(f"  [{address[:10]}...] 잔액: {readable:,.2f} {symbol}")
    return balance


def transfer_token(w3, contract, sender, receiver, amount):
    """sender에서 receiver로 토큰을 전송한다."""
    decimals = contract.functions.decimals().call()
    symbol = contract.functions.symbol().call()
    raw_amount = int(amount * (10 ** decimals))

    tx = contract.functions.transfer(receiver, raw_amount).build_transaction(
        {
            "from": sender,
            "nonce": w3.eth.get_transaction_count(sender),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
        }
    )

    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    print(f"\n[+] 토큰 전송 완료!")
    print(f"    보낸 사람 : {sender[:10]}...")
    print(f"    받는 사람 : {receiver[:10]}...")
    print(f"    전송량    : {amount:,.2f} {symbol}")
    print(f"    TX 해시   : {tx_hash.hex()}")
    print(f"    블록 번호 : {receipt.blockNumber}")

    return receipt


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

    # ── 토큰 정보 조회 ──
    get_token_info(contract)

    # ── Ganache 계정 목록 ──
    accounts = w3.eth.accounts
    deployer = accounts[0]
    receiver1 = accounts[1]
    receiver2 = accounts[2]

    # ── 전송 전 잔액 확인 ──
    print("\n[잔액 확인 — 전송 전]")
    check_balance(contract, deployer)
    check_balance(contract, receiver1)
    check_balance(contract, receiver2)

    # ── 토큰 전송 예시 ──
    print("\n--- 토큰 전송 시작 ---")

    # 배포자 → 계정1 : 500 토큰
    transfer_token(w3, contract, deployer, receiver1, 500)

    # 배포자 → 계정2 : 300 토큰
    transfer_token(w3, contract, deployer, receiver2, 300)

    # ── 전송 후 잔액 확인 ──
    print("\n[잔액 확인 — 전송 후]")
    check_balance(contract, deployer)
    check_balance(contract, receiver1)
    check_balance(contract, receiver2)

    print("\n[+] interact.py 실행 완료")


if __name__ == "__main__":
    main()
