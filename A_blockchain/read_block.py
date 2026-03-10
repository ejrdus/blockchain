# read_block.py
# Ganache GUI v2.7.2 + Web3.py 기준
# 블록 해시 / 넌스 / 트랜잭션 목록 / 가스비 / 타임스탬프 읽기
# 사용법: python read_block.py

from web3 import Web3
import json
import os
import sys

# 루트 config.py를 import하기 위해 상위 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_RPC_URL, READ_BLOCK_COUNT
import logging
from datetime import datetime, timezone

# ── 로거 설정 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ── Ganache 연결 ──────────────────────────────────────────────
def connect_ganache() -> Web3:
    """
    Ganache GUI v2.7.2 RPC에 연결합니다.
    기본 포트: 7545 (config.py에서 변경 가능)
    """
    w3 = Web3(Web3.HTTPProvider(GANACHE_RPC_URL))

    if not w3.is_connected():
        raise ConnectionError(
            f"❌ Ganache 연결 실패: {GANACHE_RPC_URL}\n"
            "→ Ganache GUI가 실행 중인지 확인하세요."
        )

    logger.info(f"✅ Ganache 연결 성공 | Chain ID: {w3.eth.chain_id}")
    return w3


# ── 단일 블록 데이터 읽기 ─────────────────────────────────────
def read_block(w3: Web3, block_identifier = "latest") -> dict:
    """
    블록 1개의 전체 데이터를 파싱해 딕셔너리로 반환합니다.

    반환 필드:
        number          블록 번호
        hash            블록 해시 (hex)
        parentHash      이전 블록 해시 (hex)
        nonce           채굴 넌스 (hex) — PoW 증명값
        timestamp       블록 생성 Unix 시각
        timestamp_str   사람이 읽기 쉬운 시각 문자열
        gasUsed         소모된 가스
        gasLimit        가스 한도
        transactions    트랜잭션 상세 목록
        tx_count        트랜잭션 수
    """
    block = w3.eth.get_block(block_identifier, full_transactions=True)

    # nonce: Ganache v2에서 bytes8 타입으로 반환됨 → hex 변환
    nonce_hex = block.nonce.hex() if isinstance(block.nonce, bytes) else str(block.nonce)

    # 트랜잭션 상세 파싱
    tx_list = []
    for tx in block.transactions:
        tx_list.append({
            "hash":          tx.hash.hex(),
            "from":          tx["from"],
            "to":            tx["to"] if tx["to"] else "Contract Creation",
            "value_eth":     float(w3.from_wei(tx["value"], "ether")),
            "gas":           tx["gas"],
            "gasPrice_gwei": float(w3.from_wei(tx["gasPrice"], "gwei")),
            "nonce":         tx["nonce"],   # 송신자 TX 넌스 (블록 넌스와 구분)
        })

    return {
        "number":        block.number,
        "hash":          block.hash.hex(),
        "parentHash":    block.parentHash.hex(),
        "nonce":         nonce_hex,
        "timestamp":     block.timestamp,
        "timestamp_str": datetime.fromtimestamp(block.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "gasUsed":       block.gasUsed,
        "gasLimit":      block.gasLimit,
        "transactions":  tx_list,
        "tx_count":      len(tx_list),
    }


# ── 최근 N개 블록 읽기 ────────────────────────────────────────
def read_recent_blocks(w3: Web3, count: int = READ_BLOCK_COUNT) -> list:
    """
    최신 블록부터 count개 블록을 읽어 리스트로 반환합니다.
    B파트 FDS 서버에 전달할 데이터 소스로 활용됩니다.
    """
    latest_num = w3.eth.block_number
    start_num  = max(0, latest_num - count + 1)

    logger.info(f"블록 읽기 범위: {start_num} ~ {latest_num} ({count}개)")

    blocks = []
    for num in range(start_num, latest_num + 1):
        data = read_block(w3, num)
        blocks.append(data)
        _print_block(data)

    return blocks


# ── 출력 포매터 ───────────────────────────────────────────────
def _print_block(info: dict) -> None:
    print("\n" + "─" * 62)
    print(f"  블록 번호    : {info['number']}")
    print(f"  블록 해시    : {info['hash']}")
    print(f"  부모 해시    : {info['parentHash']}")
    print(f"  넌스 (Nonce) : {info['nonce']}")
    print(f"  타임스탬프   : {info['timestamp_str']}")
    print(f"  Gas 사용량   : {info['gasUsed']:,} / {info['gasLimit']:,}")
    print(f"  트랜잭션 수  : {info['tx_count']}건")

    if info["tx_count"] > 0:
        print("  ── 트랜잭션 목록 ──────────────────────────────────")
        for tx in info["transactions"]:
            print(f"    TX  : {tx['hash']}")
            print(f"    From: {tx['from']}")
            print(f"    To  : {tx['to']}")
            print(f"    값  : {tx['value_eth']:.4f} ETH  |  Gas: {tx['gas']}  |  GasPrice: {tx['gasPrice_gwei']} Gwei")
            print()
    print("─" * 62)


# ── JSON 저장 (B파트 전달용) ─────────────────────────────────
def save_to_json(blocks: list, filepath: str = "blocks_output.json") -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=2)
    logger.info(f"블록 데이터 저장 완료: {filepath}")


# ── 계정 목록 출력 ────────────────────────────────────────────
def print_accounts(w3: Web3) -> None:
    print("\n Ganache 가상 지갑 계정 목록:")
    for idx, addr in enumerate(w3.eth.accounts):
        balance = w3.from_wei(w3.eth.get_balance(addr), "ether")
        print(f"   [{idx}] {addr}  ->  {balance:.2f} ETH")


# ── 메인 실행 ─────────────────────────────────────────────────
if __name__ == "__main__":
    # 1. Ganache 연결
    w3 = connect_ganache()

    # 2. 계정 목록 출력
    print_accounts(w3)

    # 3. 최근 N개 블록 읽기 (config.py의 READ_BLOCK_COUNT)
    blocks = read_recent_blocks(w3, READ_BLOCK_COUNT)

    # 4. JSON 저장 → B파트 FDS 서버 전달
    save_to_json(blocks)

    logger.info("read_block.py 완료")