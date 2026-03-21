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


# ── 특정 지갑 주소 분석 (B파트 feature 계산) ─────────────────
def analyze_address(w3: Web3, target_address: str) -> dict:
    """
    특정 지갑 주소의 모든 TX를 전수 스캔하여
    B파트 AI 모델에 필요한 feature 딕셔너리를 반환합니다.
    ETH 직접 전송 + ERC20(FDT) 토큰 Transfer 이벤트 모두 분석합니다.
    """
    target_address = w3.to_checksum_address(target_address)

    sent_times, recv_times = [], []
    sent_values, recv_values = [], []
    sent_to_addrs, recv_from_addrs = set(), set()
    sent_to_contract_values = []

    # 블록 타임스탬프 캐시 (ERC20 로그 처리 시 재활용)
    block_timestamps = {}

    latest = w3.eth.block_number
    logger.info(f"주소 분석 시작: {target_address} (블록 0 ~ {latest})")

    # ── 1) ETH 직접 전송 분석 ──
    for num in range(0, latest + 1):
        block = w3.eth.get_block(num, full_transactions=True)
        block_timestamps[num] = block.timestamp

        for tx in block.transactions:
            val = float(w3.from_wei(tx["value"], "ether"))
            ts  = block.timestamp
            frm = tx["from"]
            to  = tx["to"]

            if frm == target_address:
                sent_times.append(ts)
                sent_values.append(val)
                if to:
                    sent_to_addrs.add(to)
                    if w3.eth.get_code(to) != b"":
                        sent_to_contract_values.append(val)

            if to == target_address:
                recv_times.append(ts)
                recv_values.append(val)
                recv_from_addrs.add(frm)

    # ── 2) ERC20 Transfer 이벤트 분석 ──
    TRANSFER_TOPIC = w3.keccak(text="Transfer(address,address,uint256)")
    padded_target = '0x' + target_address[2:].lower().rjust(64, '0')

    erc20_sent_times, erc20_recv_times = [], []
    erc20_sent_values, erc20_recv_values = [], []
    erc20_sent_to_addrs = set()
    erc20_recv_from_addrs = set()
    erc20_sent_to_contract_values = []
    erc20_contract_times = []
    erc20_recv_contract_addrs = set()

    # target이 보낸 ERC20 토큰
    try:
        sent_logs = w3.eth.get_logs({
            'fromBlock': 0,
            'toBlock': 'latest',
            'topics': [TRANSFER_TOPIC, padded_target],
        })
        for log in sent_logs:
            to_addr = w3.to_checksum_address('0x' + log['topics'][2].hex()[-40:])
            raw_data = log['data']
            if len(raw_data) == 0:
                value = 0.0
            else:
                value = int.from_bytes(raw_data, 'big') / (10 ** 18)
            ts = block_timestamps.get(log['blockNumber'], 0)

            erc20_sent_times.append(ts)
            erc20_sent_values.append(value)
            erc20_sent_to_addrs.add(to_addr)

            if w3.eth.get_code(to_addr) != b"":
                erc20_sent_to_contract_values.append(value)
                erc20_contract_times.append(ts)
    except Exception as e:
        logger.warning(f"ERC20 sent 로그 조회 실패: {e}")

    # target이 받은 ERC20 토큰
    try:
        recv_logs = w3.eth.get_logs({
            'fromBlock': 0,
            'toBlock': 'latest',
            'topics': [TRANSFER_TOPIC, None, padded_target],
        })
        for log in recv_logs:
            from_addr = w3.to_checksum_address('0x' + log['topics'][1].hex()[-40:])
            raw_data = log['data']
            if len(raw_data) == 0:
                value = 0.0
            else:
                value = int.from_bytes(raw_data, 'big') / (10 ** 18)
            ts = block_timestamps.get(log['blockNumber'], 0)

            erc20_recv_times.append(ts)
            erc20_recv_values.append(value)
            erc20_recv_from_addrs.add(from_addr)

            if w3.eth.get_code(from_addr) != b"":
                erc20_recv_contract_addrs.add(from_addr)
    except Exception as e:
        logger.warning(f"ERC20 recv 로그 조회 실패: {e}")

    # ── Helper ──
    def avg_gap_min(times):
        sorted_t = sorted(times)
        if len(sorted_t) < 2:
            return 0
        gaps = [(sorted_t[i+1] - sorted_t[i]) / 60 for i in range(len(sorted_t) - 1)]
        return sum(gaps) / len(gaps)

    # ── ETH 집계 ──
    all_times = sorted(sent_times + recv_times)
    time_diff = (all_times[-1] - all_times[0]) / 60 if len(all_times) >= 2 else 0

    total_sent     = sum(sent_values)
    total_received = sum(recv_values)
    total_sent_contract = sum(sent_to_contract_values)

    # ── ERC20 집계 ──
    erc20_total_sent = sum(erc20_sent_values)
    erc20_total_recv = sum(erc20_recv_values)
    erc20_total_sent_contract = sum(erc20_sent_to_contract_values)
    erc20_total_tnxs = len(erc20_sent_times) + len(erc20_recv_times)
    has_erc20 = 1 if erc20_total_tnxs > 0 else 0

    features = {
        # ── ETH 기본 feature ──
        "Avg min between sent tnx":                    avg_gap_min(sent_times),
        "Avg min between received tnx":                avg_gap_min(recv_times),
        "Time Diff between first and last (Mins)":     time_diff,
        "Sent tnx":                                    len(sent_times),
        "Received Tnx":                                len(recv_times),
        "Number of Created Contracts":                 0,
        "Unique Received From Addresses":              len(recv_from_addrs),
        "Unique Sent To Addresses":                    len(sent_to_addrs),
        "min value received":                          min(recv_values) if recv_values else 0,
        "max value received":                          max(recv_values) if recv_values else 0,
        "avg val received":                            sum(recv_values) / len(recv_values) if recv_values else 0,
        "min val sent":                                min(sent_values) if sent_values else 0,
        "max val sent":                                max(sent_values) if sent_values else 0,
        "avg val sent":                                sum(sent_values) / len(sent_values) if sent_values else 0,
        "min value sent to contract":                  min(sent_to_contract_values) if sent_to_contract_values else 0,
        "max val sent to contract":                    max(sent_to_contract_values) if sent_to_contract_values else 0,
        "avg value sent to contract":                  sum(sent_to_contract_values) / len(sent_to_contract_values) if sent_to_contract_values else 0,
        "total transactions (including tnx to create contract": len(sent_times) + len(recv_times),
        "total Ether sent":                            total_sent,
        "total ether received":                        total_received,
        "total ether sent contracts":                  total_sent_contract,
        "total ether balance":                         total_received - total_sent,
        # ── ERC20 feature (FDT 토큰 Transfer 이벤트 기반) ──
        "Total ERC20 tnxs":                            erc20_total_tnxs,
        "ERC20 total Ether received":                  erc20_total_recv,
        "ERC20 total ether sent":                      erc20_total_sent,
        "ERC20 total Ether sent contract":             erc20_total_sent_contract,
        "ERC20 uniq sent addr":                        len(erc20_sent_to_addrs),
        "ERC20 uniq rec addr":                         len(erc20_recv_from_addrs),
        "ERC20 uniq rec contract addr":                len(erc20_recv_contract_addrs),
        "ERC20 avg time between sent tnx":             avg_gap_min(erc20_sent_times),
        "ERC20 avg time between rec tnx":              avg_gap_min(erc20_recv_times),
        "ERC20 avg time between contract tnx":         avg_gap_min(erc20_contract_times),
        "ERC20 min val rec":                           min(erc20_recv_values) if erc20_recv_values else 0,
        "ERC20 max val rec":                           max(erc20_recv_values) if erc20_recv_values else 0,
        "ERC20 avg val rec":                           sum(erc20_recv_values) / len(erc20_recv_values) if erc20_recv_values else 0,
        "ERC20 min val sent":                          min(erc20_sent_values) if erc20_sent_values else 0,
        "ERC20 max val sent":                          max(erc20_sent_values) if erc20_sent_values else 0,
        "ERC20 avg val sent":                          sum(erc20_sent_values) / len(erc20_sent_values) if erc20_sent_values else 0,
        "ERC20 min val sent contract":                 min(erc20_sent_to_contract_values) if erc20_sent_to_contract_values else 0,
        "ERC20 max val sent contract":                 max(erc20_sent_to_contract_values) if erc20_sent_to_contract_values else 0,
        "ERC20 avg val sent contract":                 sum(erc20_sent_to_contract_values) / len(erc20_sent_to_contract_values) if erc20_sent_to_contract_values else 0,
        # ── 파생 변수 ──
        "has_erc20_activity":                          has_erc20,
        "sent_received_ratio":                         total_sent / (total_received + 1e-9),
        "unique_counterparty_ratio":                   len(sent_to_addrs) / (len(sent_times) + 1e-9),
    }

    logger.info(
        f"분석 완료: ETH 송신 {len(sent_times)}건 / 수신 {len(recv_times)}건 "
        f"| ERC20 {erc20_total_tnxs}건"
    )
    return features


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