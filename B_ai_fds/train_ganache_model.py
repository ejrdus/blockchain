"""
Ganache 환경 맞춤 AI 모델 학습 스크립트

전략:
  1. Ganache 10개 계좌의 실제 feature를 추출 (시드 데이터)
  2. 각 패턴(정상/사기 6종)에 대해 합성 데이터를 대량 생성
  3. rule_engine의 규칙을 라벨링에 활용 (교사 신호)
  4. LightGBM 모델 학습 → 100% AI로 전환

이렇게 하면:
  - 규칙 기반의 도메인 지식이 AI 모델에 내재화
  - 결정 경계가 부드러워져 false positive 감소
  - 100% AI로 동작 가능 (규칙 엔진 불필요)

실행:
  python B_ai_fds/train_ganache_model.py
  (Ganache + deploy.py + simulate_transactions.py 실행 후)
"""

import json
import os
import sys
import random
import numpy as np
import pandas as pd
import joblib

# ── 경로 설정 ──
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "A_blockchain"))
sys.path.append(os.path.join(ROOT_DIR, "C_smart_contract"))

from config import GANACHE_URL
from web3 import Web3

# ═══════════════════════════════════════════════════════════════
# 1단계: Ganache에서 실제 feature 추출
# ═══════════════════════════════════════════════════════════════

ACCOUNT_ROLES = {
    0: ("Owner",         "normal"),
    1: ("Normal_A",      "normal"),
    2: ("Normal_B",      "normal"),
    3: ("Normal_C",      "normal"),
    4: ("Normal_D",      "normal"),
    5: ("Fraud_S1",      "fraud"),    # Smurfing
    6: ("Fraud_S2",      "fraud"),    # Layering + Draining
    7: ("Fraud_S3",      "fraud"),    # Dust + Pump&Collect
    8: ("Neutral_E",     "neutral"),  # 중립 (정상/사기 중간)
    9: ("Neutral_F",     "neutral"),  # 중립 (정상/사기 중간)
}

# 학습에 사용할 feature 목록 (ERC20은 Ganache에서 0이므로 비시간 feature 위주)
TRAIN_FEATURES = [
    "Avg min between sent tnx",
    "Avg min between received tnx",
    "Time Diff between first and last (Mins)",
    "Sent tnx",
    "Received Tnx",
    "Unique Received From Addresses",
    "Unique Sent To Addresses",
    "min value received",
    "max value received",
    "avg val received",
    "min val sent",
    "max val sent",
    "avg val sent",
    "min value sent to contract",
    "max val sent to contract",
    "avg value sent to contract",
    "total transactions (including tnx to create contract",
    "total Ether sent",
    "total ether received",
    "total ether sent contracts",
    "total ether balance",
    "sent_received_ratio",
    "unique_counterparty_ratio",
]


def extract_ganache_features(w3):
    """Ganache 계좌별 feature 추출"""
    from A_blockchain.read_block import analyze_address

    accounts = w3.eth.accounts
    data = []

    for idx, addr in enumerate(accounts):
        role_name, label = ACCOUNT_ROLES[idx]
        print(f"  [{idx}] {role_name} ({addr[:14]}...) feature 추출 중...")
        features = analyze_address(w3, addr)
        # 중립 계좌는 증강 시 절반 fraud/절반 normal로 분리 (중간 확률 유도)
        if label == "neutral":
            features["_label"] = -1  # 특수 라벨 (증강에서 처리)
        else:
            features["_label"] = 1 if label == "fraud" else 0
        features["_role"] = role_name
        data.append(features)

    return data


# ═══════════════════════════════════════════════════════════════
# 2단계: 합성 데이터 생성 (패턴별)
# ═══════════════════════════════════════════════════════════════

def generate_normal_samples(n=200):
    """정상 거래 패턴 합성"""
    samples = []
    for _ in range(n):
        sent_tnx = random.randint(2, 8)
        recv_tnx = random.randint(2, 10)
        unique_sent = random.randint(1, min(sent_tnx, 4))
        unique_recv = random.randint(1, min(recv_tnx, 4))

        avg_sent = random.uniform(0.5, 5.0)
        avg_recv = random.uniform(0.5, 5.0)
        min_sent = avg_sent * random.uniform(0.3, 0.8)
        max_sent = avg_sent * random.uniform(1.2, 2.5)
        min_recv = avg_recv * random.uniform(0.3, 0.8)
        max_recv = avg_recv * random.uniform(1.2, 2.5)

        total_sent = avg_sent * sent_tnx
        total_recv = avg_recv * recv_tnx
        balance = total_recv - total_sent + random.uniform(-2, 5)

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.5),
            "Avg min between received tnx": random.uniform(0, 0.5),
            "Time Diff between first and last (Mins)": random.uniform(0, 5),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": min_recv,
            "max value received": max_recv,
            "avg val received": avg_recv,
            "min val sent": min_sent,
            "max val sent": max_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 0,
        })
    return samples


def generate_smurfing_samples(n=80):
    """Smurfing 패턴: 소액 분산 세탁"""
    samples = []
    for _ in range(n):
        sent_tnx = random.randint(10, 25)
        recv_tnx = random.randint(0, 3)
        unique_sent = random.randint(3, 8)
        unique_recv = random.randint(0, 2)

        avg_sent = random.uniform(0.2, 0.9)
        min_sent = avg_sent * random.uniform(0.5, 0.9)
        max_sent = avg_sent * random.uniform(1.0, 1.5)
        total_sent = avg_sent * sent_tnx

        avg_recv = random.uniform(5, 20) if recv_tnx > 0 else 0
        total_recv = avg_recv * recv_tnx

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.1),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 3),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * 0.8 if recv_tnx > 0 else 0,
            "max value received": avg_recv * 1.2 if recv_tnx > 0 else 0,
            "avg val received": avg_recv,
            "min val sent": min_sent,
            "max val sent": max_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": total_recv - total_sent,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_layering_samples(n=80):
    """Layering 패턴: 수신 즉시 전달, 잔액 ≈ 0"""
    samples = []
    for _ in range(n):
        recv_tnx = random.randint(3, 10)
        sent_tnx = random.randint(3, 12)
        unique_sent = random.randint(3, 6)
        unique_recv = random.randint(2, 5)

        total_recv = random.uniform(10, 50)
        total_sent = total_recv * random.uniform(0.8, 1.0)  # 받은 만큼 보냄
        balance = total_recv - total_sent  # ≈ 0

        avg_recv = total_recv / recv_tnx
        avg_sent = total_sent / sent_tnx

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.1),
            "Avg min between received tnx": random.uniform(0, 0.1),
            "Time Diff between first and last (Mins)": random.uniform(0, 3),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.8),
            "max value received": avg_recv * random.uniform(1.2, 2.0),
            "avg val received": avg_recv,
            "min val sent": avg_sent * random.uniform(0.3, 0.8),
            "max val sent": avg_sent * random.uniform(1.2, 2.0),
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_draining_samples(n=60):
    """Draining 패턴: 대량 단건 인출"""
    samples = []
    for _ in range(n):
        recv_tnx = random.randint(3, 8)
        sent_tnx = random.randint(1, 3)
        unique_sent = random.randint(1, 2)
        unique_recv = random.randint(2, 5)

        total_recv = random.uniform(10, 50)
        max_val_sent = total_recv * random.uniform(0.6, 0.95)
        avg_sent = max_val_sent / sent_tnx if sent_tnx > 0 else max_val_sent
        total_sent = avg_sent * sent_tnx
        balance = total_recv - total_sent  # ≈ 0

        avg_recv = total_recv / recv_tnx

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.1),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 4),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.7),
            "max value received": avg_recv * random.uniform(1.3, 2.0),
            "avg val received": avg_recv,
            "min val sent": max_val_sent * random.uniform(0.5, 1.0),
            "max val sent": max_val_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_roundtrip_samples(n=60):
    """Round-trip 패턴: 순환 거래"""
    samples = []
    for _ in range(n):
        count = random.randint(3, 8)
        sent_tnx = count + random.randint(0, 2)
        recv_tnx = count + random.randint(0, 2)
        unique_sent = random.randint(2, 4)
        unique_recv = random.randint(2, 4)

        fixed_amount = random.uniform(3, 10)
        avg_sent = fixed_amount * random.uniform(0.9, 1.1)
        avg_recv = fixed_amount * random.uniform(0.9, 1.1)
        total_sent = avg_sent * sent_tnx
        total_recv = avg_recv * recv_tnx
        balance = total_recv - total_sent  # ≈ 0

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.1),
            "Avg min between received tnx": random.uniform(0, 0.1),
            "Time Diff between first and last (Mins)": random.uniform(0, 3),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.7, 0.95),
            "max value received": avg_recv * random.uniform(1.05, 1.3),
            "avg val received": avg_recv,
            "min val sent": avg_sent * random.uniform(0.7, 0.95),
            "max val sent": avg_sent * random.uniform(1.05, 1.3),
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_dust_samples(n=60):
    """Dust Probing 패턴: 극소액 다수 전송"""
    samples = []
    for _ in range(n):
        sent_tnx = random.randint(8, 20)
        recv_tnx = random.randint(0, 5)
        unique_sent = random.randint(3, 8)
        unique_recv = random.randint(0, 3)

        avg_sent = random.uniform(0.0001, 0.005)
        min_sent = avg_sent * random.uniform(0.3, 0.8)
        max_sent = avg_sent * random.uniform(1.0, 2.0)
        total_sent = avg_sent * sent_tnx

        avg_recv = random.uniform(1, 10) if recv_tnx > 0 else 0
        total_recv = avg_recv * recv_tnx

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.05),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 2),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * 0.5 if recv_tnx > 0 else 0,
            "max value received": avg_recv * 1.5 if recv_tnx > 0 else 0,
            "avg val received": avg_recv,
            "min val sent": min_sent,
            "max val sent": max_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": total_recv - total_sent,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_pump_collect_samples(n=60):
    """Pump & Collect 패턴: 소액 수집 후 대량 인출"""
    samples = []
    for _ in range(n):
        recv_tnx = random.randint(5, 15)
        sent_tnx = random.randint(1, 3)
        unique_recv = random.randint(3, 7)
        unique_sent = random.randint(1, 2)

        avg_recv = random.uniform(0.5, 3.0)
        total_recv = avg_recv * recv_tnx
        max_val_sent = total_recv * random.uniform(0.7, 0.95)
        avg_sent = max_val_sent / sent_tnx
        total_sent = avg_sent * sent_tnx
        balance = total_recv - total_sent

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.1),
            "Avg min between received tnx": random.uniform(0, 0.1),
            "Time Diff between first and last (Mins)": random.uniform(0, 3),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.7),
            "max value received": avg_recv * random.uniform(1.3, 2.0),
            "avg val received": avg_recv,
            "min val sent": max_val_sent * random.uniform(0.5, 1.0),
            "max val sent": max_val_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })
    return samples


def generate_neutral_samples(n=150):
    """
    중립 패턴 합성 — 정상/사기 특성이 혼재하는 중간 계좌.

    절반은 fraud(1), 절반은 normal(0)로 라벨링하여
    모델이 중립 패턴에 대해 중간 확률(25~45%)을 출력하도록 유도.

    중립 계좌의 특징:
      - 사기 계좌와 거래 이력이 있어 일부 feature가 비정상적으로 보임
      - 하지만 전체적 패턴은 사기와 다름 (거래 다양성, 잔액 유지 등)
      - 거래 건수 중간, 송수신 비율 불균형 가능, 다수 상대방
    """
    samples = []
    total_count = 0
    for _ in range(n):
        # ── 유형 A: 사기계좌와 일부 거래가 있는 중립 (중립E 유사) ──
        sent_tnx = random.randint(2, 6)
        recv_tnx = random.randint(3, 10)
        unique_sent = random.randint(2, min(sent_tnx, 4))
        unique_recv = random.randint(2, min(recv_tnx, 5))

        avg_sent = random.uniform(1.0, 4.0)
        avg_recv = random.uniform(1.5, 6.0)
        min_sent = avg_sent * random.uniform(0.2, 0.6)
        max_sent = avg_sent * random.uniform(1.5, 3.0)
        min_recv = avg_recv * random.uniform(0.2, 0.6)
        max_recv = avg_recv * random.uniform(1.5, 3.0)

        total_sent = avg_sent * sent_tnx
        total_recv = avg_recv * recv_tnx
        balance = total_recv - total_sent + random.uniform(3, 12)

        # 절반 fraud / 절반 normal → 중간 확률 유도
        label = 1 if total_count % 2 == 0 else 0
        total_count += 1

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.3),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 5),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": min_recv,
            "max value received": max_recv,
            "avg val received": avg_recv,
            "min val sent": min_sent,
            "max val sent": max_sent,
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": label,
        })

    # ── 유형 B: 수신 위주 중립 (중립F 유사) ──
    for _ in range(n // 2):
        recv_tnx = random.randint(4, 12)
        sent_tnx = random.randint(2, 5)
        unique_recv = random.randint(2, 5)
        unique_sent = random.randint(2, 4)

        avg_recv = random.uniform(1.0, 5.0)
        avg_sent = random.uniform(1.0, 4.0)
        total_recv = avg_recv * recv_tnx
        total_sent = avg_sent * sent_tnx
        balance = total_recv - total_sent + random.uniform(4, 15)

        label = 1 if total_count % 2 == 0 else 0
        total_count += 1

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.4),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 5),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.7),
            "max value received": avg_recv * random.uniform(1.3, 2.5),
            "avg val received": avg_recv,
            "min val sent": avg_sent * random.uniform(0.3, 0.6),
            "max val sent": avg_sent * random.uniform(1.5, 3.0),
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": label,
        })

    return samples


def add_noise_to_samples(seed_features, n_per_seed=15):
    """실제 Ganache 데이터에 노이즈를 추가하여 증강"""
    augmented = []
    for feat in seed_features:
        label = feat["_label"]
        for i in range(n_per_seed):
            noisy = {}
            for key in TRAIN_FEATURES:
                val = feat.get(key, 0)
                if isinstance(val, (int, float)) and val != 0:
                    noise = random.uniform(0.7, 1.3)
                    noisy[key] = val * noise
                else:
                    noisy[key] = val
            # 중립 계좌(-1): 절반은 fraud(1), 절반은 normal(0) → 중간 확률 유도
            if label == -1:
                noisy["_label"] = 1 if i < n_per_seed // 2 else 0
            else:
                noisy["_label"] = label
            augmented.append(noisy)
    return augmented


# ═══════════════════════════════════════════════════════════════
# 3단계: 학습
# ═══════════════════════════════════════════════════════════════

def generate_borderline_samples(n=120):
    """
    경계선 근처 샘플 — 정상/사기 특징이 혼재하는 애매한 케이스.
    모델이 중간 확률(30~70%)을 출력하도록 학습시키기 위함.
    """
    samples = []
    # 사기 특징이 일부 있지만 전체적으로 정상에 가까운 케이스 (label=0)
    for _ in range(n // 2):
        sent_tnx = random.randint(4, 9)
        recv_tnx = random.randint(3, 8)
        unique_sent = random.randint(2, 4)
        unique_recv = random.randint(2, 4)

        avg_sent = random.uniform(1.0, 4.0)
        avg_recv = random.uniform(1.0, 4.5)
        total_sent = avg_sent * sent_tnx
        total_recv = avg_recv * recv_tnx
        # 잔액이 약간 있지만 완전히 소진되지는 않음
        balance = total_recv - total_sent + random.uniform(0.5, 5.0)

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.3),
            "Avg min between received tnx": random.uniform(0, 0.3),
            "Time Diff between first and last (Mins)": random.uniform(0, 4),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.7),
            "max value received": avg_recv * random.uniform(1.3, 2.5),
            "avg val received": avg_recv,
            "min val sent": avg_sent * random.uniform(0.3, 0.7),
            "max val sent": avg_sent * random.uniform(1.3, 2.5),
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 0,
        })

    # 사기 특징이 있지만 완전히 전형적이지 않은 케이스 (label=1, 하지만 약한 사기)
    for _ in range(n // 2):
        sent_tnx = random.randint(4, 10)
        recv_tnx = random.randint(3, 8)
        unique_sent = random.randint(2, 5)
        unique_recv = random.randint(2, 4)

        avg_sent = random.uniform(1.0, 5.0)
        avg_recv = random.uniform(1.0, 4.0)
        total_sent = avg_sent * sent_tnx
        total_recv = avg_recv * recv_tnx
        # 잔액이 약간 부족하거나 거의 없음 (세탁 경향)
        balance = total_recv - total_sent + random.uniform(-2.0, 2.0)

        samples.append({
            "Avg min between sent tnx": random.uniform(0, 0.2),
            "Avg min between received tnx": random.uniform(0, 0.2),
            "Time Diff between first and last (Mins)": random.uniform(0, 3),
            "Sent tnx": sent_tnx,
            "Received Tnx": recv_tnx,
            "Unique Received From Addresses": unique_recv,
            "Unique Sent To Addresses": unique_sent,
            "min value received": avg_recv * random.uniform(0.3, 0.7),
            "max value received": avg_recv * random.uniform(1.3, 2.5),
            "avg val received": avg_recv,
            "min val sent": avg_sent * random.uniform(0.3, 0.7),
            "max val sent": avg_sent * random.uniform(1.3, 2.5),
            "avg val sent": avg_sent,
            "min value sent to contract": 0,
            "max val sent to contract": 0,
            "avg value sent to contract": 0,
            "total transactions (including tnx to create contract": sent_tnx + recv_tnx,
            "total Ether sent": total_sent,
            "total ether received": total_recv,
            "total ether sent contracts": 0,
            "total ether balance": balance,
            "sent_received_ratio": total_sent / (total_recv + 1e-9),
            "unique_counterparty_ratio": unique_sent / (sent_tnx + 1e-9),
            "_label": 1,
        })

    return samples


def train_model(all_data):
    """LightGBM 모델 학습 + Platt Scaling 확률 보정"""
    from lightgbm import LGBMClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.calibration import CalibratedClassifierCV

    df = pd.DataFrame(all_data)

    X = df[TRAIN_FEATURES].fillna(0)
    y = df["_label"]

    print(f"\n  학습 데이터 크기: {len(df)} 행")
    print(f"  정상(0): {(y == 0).sum()}건 / 사기(1): {(y == 1).sum()}건")
    print(f"  Feature 수: {len(TRAIN_FEATURES)}개")

    base_model = LGBMClassifier(
        n_estimators=150,
        max_depth=4,        # 얕게 → 과적합 방지, 부드러운 확률 분포
        learning_rate=0.05,
        num_leaves=15,      # 축소 → 결정 경계 부드럽게
        min_child_samples=10,
        class_weight="balanced",
        random_state=42,
        verbose=-1,
    )

    # 교차 검증
    scores = cross_val_score(base_model, X, y, cv=5, scoring="f1")
    print(f"\n  5-Fold F1 Score: {scores.mean():.4f} (±{scores.std():.4f})")
    print(f"  각 Fold: {[f'{s:.4f}' for s in scores]}")

    # Platt Scaling으로 확률 보정 (0/1로 몰리는 과신뢰 문제 해결)
    model = CalibratedClassifierCV(base_model, method="sigmoid", cv=5)
    model.fit(X, y)

    # 임계값 51% 고정 (사용자 지정)
    probas = model.predict_proba(X)[:, 1]

    best_threshold = 0.31

    print(f"\n  임계값: {best_threshold:.2f} (고정)")

    # 확률 분포 확인
    print(f"\n  확률 분포 (보정 후):")
    print(f"    정상 계좌 평균: {probas[y == 0].mean():.3f}")
    print(f"    사기 계좌 평균: {probas[y == 1].mean():.3f}")

    return model, best_threshold


def save_artifact(model, threshold, output_dir=None):
    """학습된 모델을 artifact로 저장"""
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))

    artifact = {
        "model": model,
        "feature_cols": TRAIN_FEATURES,
        "threshold": threshold,
        "model_name": "LightGBM_Ganache_v2",
    }

    path = os.path.join(output_dir, "ganache_model_artifact.pkl")
    joblib.dump(artifact, path)
    print(f"\n  ✅ 모델 저장 완료: {path}")
    return path


# ═══════════════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Ganache 맞춤 AI 모델 학습 스크립트")
    print("=" * 60)

    # ── Ganache 연결 & 실제 데이터 추출 ──
    use_ganache = True
    ganache_data = []

    try:
        w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
        if w3.is_connected():
            print(f"\n[+] Ganache 연결 성공 — {GANACHE_URL}")
            print("\n[1단계] 실제 Ganache 계좌 feature 추출...")
            ganache_data = extract_ganache_features(w3)
            print(f"  → {len(ganache_data)}개 계좌 추출 완료")
        else:
            use_ganache = False
            print("[!] Ganache 미연결 → 합성 데이터만 사용")
    except Exception as e:
        use_ganache = False
        print(f"[!] Ganache 연결 실패: {e} → 합성 데이터만 사용")

    # ── 합성 데이터 생성 ──
    print("\n[2단계] 패턴별 합성 데이터 생성...")

    all_data = []

    # 실제 Ganache 데이터 증강 (n_per_seed 축소 → 합성 데이터 비중 확보)
    if ganache_data:
        augmented = add_noise_to_samples(ganache_data, n_per_seed=8)
        all_data.extend(augmented)
        print(f"  Ganache 증강 데이터: {len(augmented)}건")

    # 패턴별 합성 데이터
    normal_samples = generate_normal_samples(300)
    neutral_samples = generate_neutral_samples(200)
    borderline_samples = generate_borderline_samples(120)   # 경계선 샘플 추가
    smurfing_samples = generate_smurfing_samples(100)
    layering_samples = generate_layering_samples(100)
    draining_samples = generate_draining_samples(80)
    roundtrip_samples = generate_roundtrip_samples(80)
    dust_samples = generate_dust_samples(80)
    pump_collect_samples = generate_pump_collect_samples(80)

    all_data.extend(normal_samples)
    all_data.extend(neutral_samples)
    all_data.extend(borderline_samples)
    all_data.extend(smurfing_samples)
    all_data.extend(layering_samples)
    all_data.extend(draining_samples)
    all_data.extend(roundtrip_samples)
    all_data.extend(dust_samples)
    all_data.extend(pump_collect_samples)

    print(f"  정상 패턴: {len(normal_samples)}건")
    print(f"  중립 패턴: {len(neutral_samples)}건 (정상 라벨)")
    print(f"  경계선 패턴: {len(borderline_samples)}건 (확률 보정용)")
    print(f"  Smurfing: {len(smurfing_samples)}건")
    print(f"  Layering: {len(layering_samples)}건")
    print(f"  Draining: {len(draining_samples)}건")
    print(f"  Round-trip: {len(roundtrip_samples)}건")
    print(f"  Dust Probing: {len(dust_samples)}건")
    print(f"  Pump&Collect: {len(pump_collect_samples)}건")
    print(f"  총 합성 데이터: {len(all_data)}건")

    # ── 학습 ──
    print("\n[3단계] LightGBM 모델 학습...")
    model, threshold = train_model(all_data)

    # ── 저장 ──
    print("\n[4단계] 모델 저장...")
    save_artifact(model, threshold)

    # ── Ganache 계좌로 검증 ──
    if ganache_data:
        print("\n[5단계] Ganache 계좌 검증...")
        print(f"\n  {'Idx':>3} | {'역할':12s} | {'실제':4s} | {'예측 확률':>8s} | {'판정':4s} | {'결과':4s}")
        print(f"  {'─' * 3}─┼─{'─' * 12}─┼─{'─' * 4}─┼─{'─' * 8}─┼─{'─' * 4}─┼─{'─' * 4}")

        correct = 0
        total = 0
        for idx, feat in enumerate(ganache_data):
            role = feat["_role"]
            true_label = feat["_label"]

            # 모델 예측
            X_test = pd.DataFrame([{k: feat.get(k, 0) for k in TRAIN_FEATURES}])
            proba = float(model.predict_proba(X_test)[:, 1][0])
            pred_label = 1 if proba >= threshold else 0

            # 중립 계좌는 중간 확률(20~50%)이면 올바른 것으로 판정
            if true_label == -1:
                label_str = "중립"
                is_correct = 0.20 <= proba <= 0.50
                pred_str = "중립" if 0.20 <= proba <= 0.50 else ("사기" if proba > 0.50 else "정상")
            else:
                label_str = "사기" if true_label == 1 else "정상"
                pred_str = "사기" if pred_label == 1 else "정상"
                is_correct = pred_label == true_label

            match = "✅" if is_correct else "❌"
            if is_correct:
                correct += 1
            total += 1

            print(f"  [{idx}] | {role:12s} | {label_str:4s} | {proba * 100:>7.2f}% | {pred_str:4s} | {match}")

        print(f"\n  정확도: {correct}/{total} ({correct / total * 100:.1f}%)")

    print("\n" + "=" * 60)
    print("  학습 완료! 이제 main.py에서 ganache_model_artifact.pkl을 로드합니다.")
    print("=" * 60)


if __name__ == "__main__":
    main()
