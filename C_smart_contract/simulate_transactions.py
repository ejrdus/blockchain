"""
거래 시뮬레이터 — Ganache 10개 계좌 간 다양한 거래 자동 생성

목적:
  A파트 analyze_address()가 의미 있는 feature를 추출할 수 있도록
  ETH 직접 전송 + FDT 에스크로 거래를 충분히 생성한다.

┌──────────────────────────────────────────────────────────────┐
│  사기 패턴 분류 (논문용)                                       │
├──────────────────────────────────────────────────────────────┤
│  1. Smurfing (소액 분산)                                      │
│     - 큰 금액을 탐지 임계값 이하 소액으로 쪼개 다수 계좌로 전송   │
│     - 특징: 송신 건수↑, 건당 금액↓, 고유 수신 주소↑             │
│     - 실사례: NoOnes 해킹($7K 이하 수백 건 출금)               │
│                                                              │
│  2. Layering (다단계 세탁)                                     │
│     - 수신 즉시 다른 계좌로 전달하는 중개 역할                    │
│     - 특징: 수신액≈송신액, 시간간격↓, sent_received_ratio≈1     │
│     - 실사례: Tornado Cash 경유 자금세탁                       │
│                                                              │
│  3. Account Draining (계좌 탈취 후 인출)                       │
│     - 짧은 시간에 잔액 전부를 1~2곳으로 빼돌림                   │
│     - 특징: 대량 단건 송신, 잔액→0, 이전 수신 이력 풍부          │
│     - 실사례: SIM-swap 후 지갑 전액 인출                       │
│                                                              │
│  4. Round-trip (순환 거래)                                     │
│     - A→B→C→A 식 순환으로 거래량 부풀리기                      │
│     - 특징: 동일 금액 반복, 송수신 주소 겹침                     │
│     - 실사례: Wash trading, 거래량 조작                        │
│                                                              │
│  5. Dust Probing (소액 탐색)                                   │
│     - 극소액(0.0001 ETH 등)을 다수 주소에 보내 활성 지갑 탐색    │
│     - 특징: 건당 금액 극소, 수신 주소 수↑↑, 총 송신액 미미       │
│     - 실사례: Dust attack (지갑 추적용)                        │
│                                                              │
│  6. Pump & Collect (소액 수집 후 대량 인출)                     │
│     - 여러 계좌에서 소액 수집 → 한 번에 대량 송금               │
│     - 특징: 수신 건수↑, 고유 송신 주소↑, 이후 대량 단건 송신    │
│     - 실사례: 피싱 수익금 모으기, 투자 사기 수금                 │
│                                                              │
│  * 정상 패턴: 소액 분산, 양방향, 규칙적, 수신 위주 등            │
│  * 중립 패턴: 정상+의심 혼합                                   │
└──────────────────────────────────────────────────────────────┘

계좌 역할:
  [0] 배포자/owner     — FDT 분배, 에스크로 승인/거부 권한
  [1] 정상 유저 A       — 소액 분산 전송, 다양한 상대
  [2] 정상 유저 B       — 규칙적 소액, 양방향 거래
  [3] 정상 유저 C       — 수신 위주 (급여 수령형)
  [4] 정상 유저 D       — 적은 거래, 안정적
  [5] 사기 계좌 S1      — Smurfing (소액 분산 세탁)
  [6] 사기 계좌 S2      — Layering (다단계 세탁) + Account Draining
  [7] 사기 계좌 S3      — Dust Probing + Pump & Collect
  [8] 중립 유저 E       — 혼합 패턴
  [9] 중립 유저 F       — 혼합 패턴 + Round-trip 참여

실행:
  python C_smart_contract/simulate_transactions.py
  (deploy.py 실행 후, interact.py 실행 전에 돌리세요)
"""

import json
import os
import sys
import random

from web3 import Web3

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import GANACHE_URL

BASE_DIR = os.path.dirname(__file__)


# ═══════════════════════════════════════════════════════════════
# 유틸 함수
# ═══════════════════════════════════════════════════════════════

def send_eth(w3, sender, receiver, amount_eth, label=""):
    """ETH 직접 전송"""
    tx = {
        "from": sender,
        "to": receiver,
        "value": w3.to_wei(amount_eth, "ether"),
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(sender),
    }
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    tag = f" [{label}]" if label else ""
    print(f"  ETH{tag} {sender[:8]}→{receiver[:8]} : {amount_eth:.4f} ETH  (블록 {receipt.blockNumber})")
    return receipt


def load_contract(w3):
    """배포된 FDT 컨트랙트 로드"""
    deploy_info_path = os.path.join(BASE_DIR, "deploy_info.json")
    if not os.path.exists(deploy_info_path):
        print("[!] deploy_info.json 없음. deploy.py를 먼저 실행하세요.")
        sys.exit(1)

    with open(deploy_info_path, "r") as f:
        deploy_info = json.load(f)

    abi_path = os.path.join(BASE_DIR, "abi", "Token.json")
    with open(abi_path, "r") as f:
        abi = json.load(f)

    contract = w3.eth.contract(
        address=deploy_info["contract_address"], abi=abi
    )
    return contract


def transfer_fdt(w3, contract, sender, receiver, amount_fdt, label=""):
    """FDT ERC-20 직접 전송"""
    decimals = contract.functions.decimals().call()
    raw = int(amount_fdt * (10 ** decimals))

    tx = contract.functions.transfer(receiver, raw).build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    tag = f" [{label}]" if label else ""
    print(f"  FDT{tag} {sender[:8]}→{receiver[:8]} : {amount_fdt:,.0f} FDT  (블록 {receipt.blockNumber})")
    return receipt


def escrow_deposit(w3, contract, sender, receiver, amount_fdt, label=""):
    """에스크로 예치"""
    decimals = contract.functions.decimals().call()
    raw = int(amount_fdt * (10 ** decimals))

    tx = contract.functions.escrowDeposit(receiver, raw).build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    logs = contract.events.EscrowDeposited().process_receipt(receipt)
    tx_id = logs[0]["args"]["txId"]
    tag = f" [{label}]" if label else ""
    print(f"  ESCROW{tag} 예치 #{tx_id} : {sender[:8]}→{receiver[:8]} {amount_fdt:,.0f} FDT")
    return tx_id


def escrow_approve(w3, contract, owner, tx_id):
    """에스크로 승인"""
    tx = contract.functions.escrowApprove(tx_id).build_transaction({
        "from": owner,
        "nonce": w3.eth.get_transaction_count(owner),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  ESCROW ✅ 승인 #{tx_id}")


def escrow_reject(w3, contract, owner, tx_id):
    """에스크로 거부"""
    tx = contract.functions.escrowReject(tx_id).build_transaction({
        "from": owner,
        "nonce": w3.eth.get_transaction_count(owner),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  ESCROW ⚠️  거부 #{tx_id}")


# ═══════════════════════════════════════════════════════════════
# 패턴별 시뮬레이션 함수
# ═══════════════════════════════════════════════════════════════

def pattern_normal(w3, accts):
    """
    정상 패턴 — 일반적인 개인 간 송금 행위

    특징:
      - 소액(0.3~3 ETH) 분산 전송
      - 다양한 상대방 (Unique Sent To Addresses ↑)
      - 양방향 거래 존재 (상호 송금)
      - 거래 간격이 일정
      - sent_received_ratio가 1 근처
    """
    print(f"\n{'='*60}")
    print("  정상 패턴: 소액 분산 + 양방향 + 규칙적")
    print("=" * 60)

    # 정상 A [1]: 5명에게 소액 분산 — unique_counterparty_ratio ↑
    for target in [accts[2], accts[3], accts[4], accts[8], accts[9]]:
        amt = round(random.uniform(0.5, 3.0), 4)
        send_eth(w3, accts[1], target, amt, "정상A 분산 송금")

    # 정상 B [2]: 규칙적 소액 — avg val sent 안정
    for target in [accts[1], accts[3], accts[4]]:
        amt = round(random.uniform(1.0, 2.0), 4)
        send_eth(w3, accts[2], target, amt, "정상B 규칙 소액")

    # 정상 C [3]: 수신 위주, 가끔 소액 전송 — Received Tnx >> Sent tnx
    send_eth(w3, accts[3], accts[1], 0.5, "정상C 소액 1건")
    send_eth(w3, accts[3], accts[2], 0.3, "정상C 소액 1건")

    # 정상 D [4]: 적은 거래, 안정적 금액 — total transactions ↓
    send_eth(w3, accts[4], accts[1], 1.0, "정상D 안정")
    send_eth(w3, accts[4], accts[2], 1.5, "정상D 안정")

    # 양방향 상호 전송 — 정상적인 결제/정산 패턴
    send_eth(w3, accts[1], accts[2], 2.0, "정상 상호거래")
    send_eth(w3, accts[2], accts[1], 1.8, "정상 상호거래")
    send_eth(w3, accts[3], accts[4], 0.7, "정상 상호거래")
    send_eth(w3, accts[4], accts[3], 0.9, "정상 상호거래")
    send_eth(w3, accts[1], accts[3], 1.2, "정상 상호거래")
    send_eth(w3, accts[3], accts[1], 1.0, "정상 상호거래")


def pattern_smurfing(w3, accts):
    """
    사기 패턴 1: Smurfing (소액 분산 세탁)

    수법:
      큰 금액을 탐지 임계값 이하의 소액으로 쪼개서
      다수의 계좌에 분산 전송하여 탐지를 회피한다.

    실사례:
      NoOnes 해킹 (2025.01) — $7,000 이하로 수백 건 출금

    탐지 feature:
      - Sent tnx ↑↑ (송신 건수 비정상적으로 많음)
      - avg val sent ↓↓ (건당 금액 매우 작음)
      - Unique Sent To Addresses ↑↑ (수신 주소 다양)
      - total Ether sent ↑ (총합은 큼)
      - unique_counterparty_ratio ≈ 1 (거래마다 다른 상대)
      - min val sent ≈ max val sent (금액 균일)

    accounts[5] (S1) 사용
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 1: Smurfing (소액 분산 세탁)")
    print("  → 큰 금액을 소액으로 쪼개 다수 계좌에 분산")
    print("=" * 60)

    # S1 → 사기 계좌끼리만 소액 분산 (정상 계좌 오염 방지)
    smurfing_targets = [
        accts[6], accts[7], accts[8], accts[9],
        accts[6], accts[7], accts[8], accts[9],
        accts[6], accts[7], accts[8], accts[9],
        accts[6], accts[7], accts[9],
    ]
    for target in smurfing_targets:
        amt = round(random.uniform(0.3, 0.8), 4)
        send_eth(w3, accts[5], target, amt, "Smurfing 소액분산")

    # S1 → S2에도 소액 분산 (세탁 계좌 간 이동)
    for _ in range(5):
        amt = round(random.uniform(0.4, 0.7), 4)
        send_eth(w3, accts[5], accts[6], amt, "Smurfing→세탁계좌")


def pattern_layering(w3, accts):
    """
    사기 패턴 2: Layering (다단계 세탁)

    수법:
      자금을 받자마자 즉시 다른 계좌로 전달하는 중개 역할.
      여러 단계를 거쳐 자금 출처를 추적하기 어렵게 만든다.

    실사례:
      Tornado Cash 경유 세탁, Lazarus 그룹 다단계 믹싱

    탐지 feature:
      - total ether received ≈ total Ether sent (받은 만큼 보냄)
      - sent_received_ratio ≈ 1.0
      - Avg min between sent tnx ≈ 0 (즉시 전달)
      - Avg min between received tnx ≈ 0 (연속 수신)
      - Time Diff between first and last (Mins) ↓ (짧은 활동 기간)
      - total ether balance ≈ 0 (잔액 거의 없음)

    accounts[6] (S2) 사용
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 2: Layering (다단계 세탁)")
    print("  → 수신 즉시 다른 곳으로 전달 (중개/세탁)")
    print("=" * 60)

    # 먼저 S2에게 자금 모으기 (여러 출처에서 수신)
    send_eth(w3, accts[5], accts[6], 5.0, "Layering 유입1")
    send_eth(w3, accts[7], accts[6], 8.0, "Layering 유입2")
    send_eth(w3, accts[8], accts[6], 3.0, "Layering 유입3")

    # S2가 수신 즉시 사기/중립 계좌로 분산 전달 (정상 계좌 오염 방지)
    send_eth(w3, accts[6], accts[7], 4.0, "Layering 즉시전달")
    send_eth(w3, accts[6], accts[9], 3.5, "Layering 즉시전달")
    send_eth(w3, accts[6], accts[5], 3.0, "Layering 즉시전달")
    send_eth(w3, accts[6], accts[8], 2.5, "Layering 즉시전달")
    send_eth(w3, accts[6], accts[7], 2.0, "Layering 즉시전달")

    # 2차 유입 → 즉시 전달 반복
    send_eth(w3, accts[5], accts[6], 6.0, "Layering 2차 유입")
    send_eth(w3, accts[6], accts[8], 3.0, "Layering 2차 전달")
    send_eth(w3, accts[6], accts[9], 2.8, "Layering 2차 전달")


def pattern_account_draining(w3, accts):
    """
    사기 패턴 3: Account Draining (계좌 탈취 후 전액 인출)

    수법:
      계좌를 탈취한 후 잔액 전부를 1~2개 계좌로 빼돌린다.
      SIM-swap, 피싱 등으로 접근 권한 획득 후 실행.

    실사례:
      SIM-swap 지갑 전액 인출 ($410M 피해, 2024)

    탐지 feature:
      - max val sent ↑↑ (한 번에 대량 송신)
      - Sent tnx 1~2건 (적은 횟수로 전액)
      - total ether balance → 0에 가까움
      - Received Tnx ↑ (이전에 정상 수신 이력 있음)
      - Unique Sent To Addresses = 1~2 (빼돌릴 곳만)

    accounts[6] (S2) 추가 사용 — Layering 후 남은 잔액 탈취 시나리오
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 3: Account Draining (전액 인출)")
    print("  → 짧은 시간에 잔액 전부를 1~2곳으로 빼돌림")
    print("=" * 60)

    # S2가 보유한 잔액 대부분을 S3 한 곳으로 대량 전송
    send_eth(w3, accts[6], accts[7], 10.0, "Draining 대량인출1")
    send_eth(w3, accts[6], accts[7], 8.0,  "Draining 대량인출2")


def pattern_roundtrip(w3, accts):
    """
    사기 패턴 4: Round-trip (순환 거래)

    수법:
      A→B→C→A 순환으로 거래량을 부풀려 활성 계좌로 위장하거나
      wash trading으로 시장을 조작한다.

    실사례:
      Wash trading (NFT, DEX), 거래량 부풀리기

    탐지 feature:
      - 송수신 상대 주소 겹침 (같은 주소가 sent/received 모두에 등장)
      - Sent tnx ≈ Received Tnx (보낸 만큼 받음)
      - total ether balance 변화 거의 없음 (수수료만 감소)
      - 동일 금액 반복 (min val sent ≈ max val sent ≈ avg val sent)

    accounts[5],[7],[9] 사용 — 삼각 순환
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 4: Round-trip (순환 거래)")
    print("  → A→B→C→A 순환으로 거래량 부풀리기")
    print("=" * 60)

    # 순환 1: S1 → S3 → F → S1 (동일 금액 5 ETH)
    send_eth(w3, accts[5], accts[7], 5.0, "Round-trip 순환1")
    send_eth(w3, accts[7], accts[9], 5.0, "Round-trip 순환1")
    send_eth(w3, accts[9], accts[5], 5.0, "Round-trip 순환1")

    # 순환 2: 다시 반복 (금액 동일)
    send_eth(w3, accts[5], accts[7], 5.0, "Round-trip 순환2")
    send_eth(w3, accts[7], accts[9], 5.0, "Round-trip 순환2")
    send_eth(w3, accts[9], accts[5], 5.0, "Round-trip 순환2")

    # 순환 3: 금액 변경 (3 ETH)
    send_eth(w3, accts[5], accts[7], 3.0, "Round-trip 순환3")
    send_eth(w3, accts[7], accts[9], 3.0, "Round-trip 순환3")
    send_eth(w3, accts[9], accts[5], 3.0, "Round-trip 순환3")


def pattern_dust_probing(w3, accts):
    """
    사기 패턴 5: Dust Probing (소액 탐색 공격)

    수법:
      극소액(0.0001~0.001 ETH)을 다수의 주소에 전송하여
      어떤 지갑이 활성 상태인지 탐색한다.
      이후 활성 지갑을 대상으로 피싱/사기를 시도.

    실사례:
      Dust attack — 비트코인/이더리움 지갑 추적

    탐지 feature:
      - avg val sent ↓↓↓ (극소액)
      - min val sent ≈ max val sent ≈ 0에 가까움
      - Sent tnx ↑↑ (많은 건수)
      - Unique Sent To Addresses ↑↑ (다수 주소에 전송)
      - total Ether sent ↓ (총합은 미미)
      - total ether balance 큰 변화 없음

    accounts[7] (S3) 사용
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 5: Dust Probing (소액 탐색 공격)")
    print("  → 극소액을 다수 주소에 전송하여 활성 지갑 탐색")
    print("=" * 60)

    # S3 → 사기/중립 계좌에만 극소액 전송 (정상 계좌 오염 방지)
    dust_targets = [accts[5], accts[6], accts[8], accts[9],
                    accts[5], accts[6], accts[8], accts[9]]
    for target in dust_targets:
        amt = round(random.uniform(0.0001, 0.001), 4)
        send_eth(w3, accts[7], target, amt, "Dust 탐색")

    # 반복 (2차 탐색)
    for target in dust_targets:
        amt = round(random.uniform(0.0001, 0.0008), 4)
        send_eth(w3, accts[7], target, amt, "Dust 2차 탐색")


def pattern_pump_collect(w3, accts):
    """
    사기 패턴 6: Pump & Collect (소액 수집 후 대량 인출)

    수법:
      여러 피해 계좌에서 소액씩 수집(피싱, 투자사기 수금)한 후
      모은 자금을 한 번에 대량으로 빼돌린다.

    실사례:
      피싱 수익금 모으기, Pig Butchering 수금

    탐지 feature:
      - Received Tnx ↑↑ (여러 곳에서 수신)
      - Unique Received From Addresses ↑↑
      - avg val received ↓ (소액 수신)
      - 이후 Sent tnx 1~2건, max val sent ↑↑ (대량 송신)
      - total ether balance → 0 (수집 후 전부 인출)

    accounts[7] (S3) 사용 — Dust 이후 수집+인출 역할
    """
    print(f"\n{'='*60}")
    print("  사기 패턴 6: Pump & Collect (소액 수집 후 대량 인출)")
    print("  → 여러 곳에서 소액 수집 → 한 번에 대량 송금")
    print("=" * 60)

    # 사기/중립 계좌에서 S3로 소액 입금 (정상 계좌는 수금 피해 제외)
    collectors = [accts[5], accts[6], accts[8], accts[9], accts[5], accts[6]]
    for source in collectors:
        amt = round(random.uniform(0.5, 2.0), 4)
        send_eth(w3, source, accts[7], amt, "Collect 소액수집")

    # 모은 돈을 한 번에 S1으로 대량 인출
    send_eth(w3, accts[7], accts[5], 15.0, "Collect→대량인출")


def pattern_neutral(w3, accts):
    """
    중립 패턴 — 정상+의심 행위 혼합

    accounts[8] (E), accounts[9] (F) 사용
    """
    print(f"\n{'='*60}")
    print("  중립 패턴: 정상+의심 혼합")
    print("=" * 60)

    # 중립 E [8]: 중립끼리 + 사기 계좌와도 거래 (정상 계좌 오염 최소화)
    send_eth(w3, accts[8], accts[9], 2.0, "중립E→중립F")
    send_eth(w3, accts[8], accts[5], 4.0, "중립E→사기계좌")
    send_eth(w3, accts[8], accts[6], 1.5, "중립E→세탁계좌")
    send_eth(w3, accts[8], accts[9], 1.0, "중립E→중립F")

    # 중립 F [9]: 소수 거래 + 의심 계좌와 혼합
    send_eth(w3, accts[9], accts[8], 3.0, "중립F→중립E")
    send_eth(w3, accts[9], accts[6], 2.5, "중립F→세탁계좌")
    send_eth(w3, accts[9], accts[5], 2.0, "중립F→사기계좌")


def pattern_escrow_mixed(w3, contract, accts, owner):
    """
    FDT 에스크로 거래 — 정상/사기 혼합

    정상: 소액 분산, 다양한 상대
    사기: 대량 집중, Smurfing 소액 분산, 순환
    """
    print(f"\n{'='*60}")
    print("  FDT 에스크로 거래 (정상 + 사기 혼합)")
    print("=" * 60)

    escrow_txs = []

    # ── 정상 유저 에스크로 ──
    print("\n  ── 정상 에스크로 (소액, 분산, 다양한 상대) ──")
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[1], accts[2], 500,  "정상A→B")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[1], accts[3], 300,  "정상A→C")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[2], accts[4], 200,  "정상B→D")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[4], accts[1], 150,  "정상D→A")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[1], accts[4], 700,  "정상A→D")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[2], accts[3], 400,  "정상B→C")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[3], accts[1], 100,  "정상C→A")))

    # ── Smurfing 에스크로 (소액 분산 — 사기/중립 계좌만 대상) ──
    print("\n  ── Smurfing 에스크로 (임계값 이하 소액 반복) ──")
    for target in [accts[6], accts[7], accts[8], accts[9], accts[6], accts[7]]:
        amt = random.randint(50, 150)
        escrow_txs.append(("fraud", escrow_deposit(w3, contract, accts[5], target, amt, "Smurfing 소액")))

    # ── 대량 집중 에스크로 ──
    print("\n  ── 대량 집중 에스크로 (한 상대에게 반복 대량) ──")
    escrow_txs.append(("fraud", escrow_deposit(w3, contract, accts[5], accts[6], 15000, "대량집중")))
    escrow_txs.append(("fraud", escrow_deposit(w3, contract, accts[5], accts[6], 12000, "대량집중")))

    # ── 순환 에스크로 ──
    print("\n  ── 순환 에스크로 (Round-trip) ──")
    escrow_txs.append(("fraud", escrow_deposit(w3, contract, accts[6], accts[7], 5000, "순환 S2→S3")))
    escrow_txs.append(("fraud", escrow_deposit(w3, contract, accts[7], accts[5], 5000, "순환 S3→S1")))

    # ── 중립 에스크로 (중립끼리 + 사기 계좌 혼합) ──
    print("\n  ── 중립 에스크로 ──")
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[8], accts[9], 1000, "중립E→F")))
    escrow_txs.append(("normal", escrow_deposit(w3, contract, accts[9], accts[8], 800,  "중립F→E")))
    escrow_txs.append(("fraud",  escrow_deposit(w3, contract, accts[8], accts[5], 3000, "중립E→사기")))

    # ── 승인/거부 처리 ──
    print(f"\n{'─'*60}")
    print("  에스크로 승인/거부 처리")
    print("─" * 60)

    for category, tx_id in escrow_txs:
        if category == "normal":
            escrow_approve(w3, contract, owner, tx_id)
        else:
            escrow_reject(w3, contract, owner, tx_id)


# ═══════════════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════════════

def main():
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    if not w3.is_connected():
        print("[!] Ganache 연결 실패")
        sys.exit(1)
    print(f"[+] Ganache 연결 — {GANACHE_URL}")

    accts = w3.eth.accounts
    owner = accts[0]

    contract = load_contract(w3)
    symbol = contract.functions.symbol().call()
    print(f"[+] 컨트랙트 로드 — {symbol}\n")

    # ── Phase 1: FDT 토큰 초기 분배 ──
    print("=" * 60)
    print("  Phase 1: FDT 토큰 초기 분배")
    print("=" * 60)

    distributions = {
        accts[1]: 50000,   # 정상 A
        accts[2]: 30000,   # 정상 B
        accts[3]: 10000,   # 정상 C
        accts[4]: 20000,   # 정상 D
        accts[5]: 80000,   # S1 Smurfing
        accts[6]: 60000,   # S2 Layering
        accts[7]: 70000,   # S3 Dust + Collect
        accts[8]: 25000,   # 중립 E
        accts[9]: 15000,   # 중립 F
    }

    for addr, amount in distributions.items():
        transfer_fdt(w3, contract, owner, addr, amount)

    # ── Phase 2~7: 패턴별 ETH 거래 ──
    pattern_normal(w3, accts)          # 정상 패턴
    pattern_smurfing(w3, accts)        # 사기1: Smurfing
    pattern_layering(w3, accts)        # 사기2: Layering
    pattern_account_draining(w3, accts)# 사기3: Account Draining
    pattern_roundtrip(w3, accts)       # 사기4: Round-trip
    pattern_dust_probing(w3, accts)    # 사기5: Dust Probing
    pattern_pump_collect(w3, accts)    # 사기6: Pump & Collect
    pattern_neutral(w3, accts)         # 중립 패턴

    # ── Phase 8: FDT 에스크로 거래 ──
    pattern_escrow_mixed(w3, contract, accts, owner)

    # ══════════════════════════════════════════════════════════
    # 결과 요약
    # ══════════════════════════════════════════════════════════
    latest_block = w3.eth.block_number
    total_txs = 0
    for num in range(0, latest_block + 1):
        block = w3.eth.get_block(num)
        total_txs += len(block.transactions)

    print(f"\n{'='*60}")
    print(f"  시뮬레이션 완료!")
    print(f"  총 블록 수     : {latest_block + 1}")
    print(f"  총 트랜잭션 수 : {total_txs}")
    print(f"{'='*60}")

    labels = ["배포자", "정상A", "정상B", "정상C", "정상D",
              "사기S1", "사기S2", "사기S3", "중립E", "중립F"]
    roles  = ["Owner", "Normal", "Normal", "Normal", "Normal",
              "Smurf", "Layer", "Dust/Collect", "Neutral", "Neutral"]

    print(f"\n  {'Idx':>3} | {'역할':6s} | {'유형':12s} | {'주소':14s} | {'ETH':>10s} | {'FDT':>12s}")
    print(f"  {'─'*3}─┼─{'─'*6}─┼─{'─'*12}─┼─{'─'*14}─┼─{'─'*10}─┼─{'─'*12}")
    for i, addr in enumerate(accts):
        eth_bal = float(w3.from_wei(w3.eth.get_balance(addr), "ether"))
        fdt_bal = contract.functions.balanceOf(addr).call() / (10 ** 18)
        print(f"  [{i:1d}] | {labels[i]:6s} | {roles[i]:12s} | {addr[:14]}| {eth_bal:>10.2f} | {fdt_bal:>12,.0f}")

    # ── 패턴 요약 출력 ──
    print(f"\n{'='*60}")
    print("  구현된 사기 패턴 요약 (논문용)")
    print("=" * 60)
    patterns = [
        ("1. Smurfing",         "S1 [5]", "소액 분산 세탁 (임계값 이하 반복)"),
        ("2. Layering",         "S2 [6]", "수신 즉시 다단계 전달 (세탁)"),
        ("3. Account Draining", "S2 [6]", "전액 1~2곳으로 인출 (탈취)"),
        ("4. Round-trip",       "S1,S3,F", "순환 거래로 거래량 부풀리기"),
        ("5. Dust Probing",     "S3 [7]", "극소액 다수 전송 (활성 지갑 탐색)"),
        ("6. Pump & Collect",   "S3 [7]", "소액 수집 후 대량 인출 (피싱 수금)"),
    ]
    for name, who, desc in patterns:
        print(f"  {name:22s} | {who:8s} | {desc}")

    print(f"\n[+] simulate_transactions.py 완료")
    print("[*] 이제 interact.py를 실행하면 에스크로+AI 검증 데모를 할 수 있습니다.")


if __name__ == "__main__":
    main()
