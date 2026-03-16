"""
C파트 — 스마트 컨트랙트 배포 스크립트
Ganache에 Token 컨트랙트를 컴파일 및 배포한다.
Remix IDE 대신 solcx를 사용하여 Python으로 처리.
"""

import json
import os
import sys

from solcx import compile_standard, install_solc
from web3 import Web3

# 루트 config.py를 import하기 위해 상위 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_URL

# ── 1) Solidity 컴파일러 설치 및 소스 읽기 ──
SOLC_VERSION = "0.8.0"
install_solc(SOLC_VERSION)

CONTRACT_DIR = os.path.join(os.path.dirname(__file__), "contracts")
ABI_DIR = os.path.join(os.path.dirname(__file__), "abi")

with open(os.path.join(CONTRACT_DIR, "Token.sol"), "r") as f:
    token_source = f.read()

# ── 2) 컴파일 ──
compiled = compile_standard(
    {
        "language": "Solidity",
        "sources": {"Token.sol": {"content": token_source}},
        "settings": {
            "outputSelection": {
                "*": {"*": ["abi", "metadata", "evm.bytecode"]}
            }
        },
    },
    solc_version=SOLC_VERSION,
)

contract_data = compiled["contracts"]["Token.sol"]["Token"]
abi = contract_data["abi"]
bytecode = contract_data["evm"]["bytecode"]["object"]

# ── 3) ABI 파일 저장 ──
os.makedirs(ABI_DIR, exist_ok=True)
abi_path = os.path.join(ABI_DIR, "Token.json")
with open(abi_path, "w") as f:
    json.dump(abi, f, indent=2)
print(f"[+] ABI 저장 완료 → {abi_path}")

# ── 4) Ganache 연결 및 배포 ──
w3 = Web3(Web3.HTTPProvider(GANACHE_URL))

if not w3.is_connected():
    print("[!] Ganache에 연결할 수 없습니다. Ganache가 실행 중인지 확인하세요.")
    sys.exit(1)

print(f"[+] Ganache 연결 성공 — {GANACHE_URL}")

# 배포에 사용할 계정 (Ganache 첫 번째 계정)
deployer = w3.eth.accounts[0]
print(f"[+] 배포 계정: {deployer}")

# 컨트랙트 객체 생성
TokenContract = w3.eth.contract(abi=abi, bytecode=bytecode)

# 초기 발행량: 1,000,000 토큰
INITIAL_SUPPLY = 1_000_000

# 트랜잭션 생성 및 전송
tx = TokenContract.constructor(INITIAL_SUPPLY).build_transaction(
    {
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 3_000_000,
        "gasPrice": w3.eth.gas_price,
    }
)

tx_hash = w3.eth.send_transaction(tx)
tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

contract_address = tx_receipt.contractAddress
print(f"[+] 컨트랙트 배포 완료!")
print(f"    주소: {contract_address}")
print(f"    트랜잭션 해시: {tx_hash.hex()}")
print(f"    블록 번호: {tx_receipt.blockNumber}")

# ── 5) 배포 정보 저장 (interact.py에서 사용) ──
deploy_info = {
    "contract_address": contract_address,
    "deployer": deployer,
    "initial_supply": INITIAL_SUPPLY,
    "tx_hash": tx_hash.hex(),
    "block_number": tx_receipt.blockNumber,
}

deploy_info_path = os.path.join(os.path.dirname(__file__), "deploy_info.json")
with open(deploy_info_path, "w") as f:
    json.dump(deploy_info, f, indent=2)
print(f"[+] 배포 정보 저장 → {deploy_info_path}")
