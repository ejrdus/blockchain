"""
규칙 기반 사기 탐지 엔진 (Rule-Based Fraud Detection)

AI 모델은 실제 이더리움 데이터로 학습되어 로컬 Ganache 환경에서는
feature 분포(특히 시간 간격, 거래 규모)가 달라 정확도가 떨어진다.

이 엔진은 analyze_address()가 추출한 feature를 기반으로
6가지 사기 패턴에 대한 규칙 기반 점수를 계산한다.

최종 판별 = AI 모델 결과 + 규칙 기반 결과 (가중 평균)

┌──────────────────────────────────────────────────────────┐
│  규칙 기반 탐지 패턴                                       │
├──────────────────────────────────────────────────────────┤
│  1. Smurfing    : 송신 多 + 건당 소액 + 총 거래량 높음      │
│  2. Layering    : 대량 수신·송신 + 잔액≈0 + 빠른 전달      │
│  3. Draining    : 대량 단건 송신 + 수신자 1~2개            │
│  4. Round-trip  : 송신≈수신 + 잔액 변화 없음              │
│  5. Dust        : 극소액 多건 전송                         │
│  6. Pump&Collect: 소액 多수신 + 대량 단건 송신             │
└──────────────────────────────────────────────────────────┘

Ganache 환경 임계값 (10개 계좌 시뮬레이션 기준):
  - 정상 계좌: Sent 26~64건, Total txn 52~88, Total ETH sent 14~55
  - 사기 계좌: Sent 81~195건, Total txn 155~250, Total ETH sent 131~176
  - 중립 계좌: Sent 36~48건, Total txn 99~131, Total ETH sent 28~90
"""


def detect_smurfing(features: dict) -> tuple[float, str]:
    """
    패턴 1: Smurfing (소액 분산 세탁)
    - 송신 건수 비정상적으로 많음 (≥ 80)
    - 총 거래 건수 매우 높음 (≥ 140)
    - 총 송신 ETH 높음 (≥ 100)
    """
    score = 0.0

    sent_tnx = features.get("Sent tnx", 0)
    total_txn = features.get("total transactions (including tnx to create contract", 0)
    total_sent = features.get("total Ether sent", 0)
    avg_val_sent = features.get("avg val sent", 0)

    # 송신 건수 비정상적으로 높음 (정상 max=64, 사기 min=81)
    if sent_tnx >= 80:
        score += 35
    elif sent_tnx >= 60:
        score += 15

    # 총 거래 건수 비정상적으로 높음 (정상 max=88, 사기 min=155)
    if total_txn >= 140:
        score += 30
    elif total_txn >= 100:
        score += 15

    # 총 송신 ETH 높음 (정상 max=55, 사기 min=131)
    if total_sent >= 100:
        score += 25
    elif total_sent >= 60:
        score += 10

    # 건당 소액이면서 대량 거래 (소액 분산 핵심 지표)
    if 0 < avg_val_sent <= 1.5 and sent_tnx >= 70:
        score += 10

    return min(score, 100), "Smurfing" if score >= 50 else ""


def detect_layering(features: dict) -> tuple[float, str]:
    """
    패턴 2: Layering (다단계 세탁)
    - 총 수신·송신 모두 대규모 (> 100 ETH)
    - 수신 건수 비정상 높음 (≥ 40)
    - 잔액 대비 거래량 비율 높음
    """
    score = 0.0

    total_sent = features.get("total Ether sent", 0)
    total_received = features.get("total ether received", 0)
    balance = features.get("total ether balance", 0)
    recv_tnx = features.get("Received Tnx", 0)
    sent_tnx = features.get("Sent tnx", 0)

    # 수신·송신 모두 대규모 (중개자 특성)
    if total_sent >= 100 and total_received >= 100:
        score += 35
    elif total_sent >= 60 and total_received >= 60:
        score += 15

    # 수신 건수 비정상 높음 (정상 max=26, 사기 min=55)
    if recv_tnx >= 50:
        score += 25
    elif recv_tnx >= 35:
        score += 10

    # 잔액 대비 거래 규모 비율 (다 전달했다는 의미)
    if total_received > 0 and abs(balance) < total_received * 0.3:
        score += 15

    # 송신도 수신도 건수 많음 (중개)
    if sent_tnx >= 50 and recv_tnx >= 40:
        score += 15

    return min(score, 100), "Layering" if score >= 50 else ""


def detect_draining(features: dict) -> tuple[float, str]:
    """
    패턴 3: Account Draining (전액 인출)
    - 대량 단건 송신 (max val sent 높음)
    - 송신 건수 적음 (1~3건)
    - 수신자 1~2개
    - 잔액 → 0
    """
    score = 0.0

    max_val_sent = features.get("max val sent", 0)
    sent_tnx = features.get("Sent tnx", 0)
    unique_sent_to = features.get("Unique Sent To Addresses", 0)
    balance = features.get("total ether balance", 0)
    total_received = features.get("total ether received", 0)
    recv_tnx = features.get("Received Tnx", 0)

    # 대량 송신
    if max_val_sent >= 15:
        score += 30
    elif max_val_sent >= 8:
        score += 15

    # 송신 건수 적음 + 수신자 적음 = 빼돌리기
    if 1 <= sent_tnx <= 5 and unique_sent_to <= 2:
        score += 25

    # 잔액 거의 0
    if total_received > 0 and balance < total_received * 0.1:
        score += 20

    # 이전에 수신 이력은 있음 (탈취 전 정상 사용)
    if recv_tnx >= 5 and sent_tnx <= 5:
        score += 15

    return min(score, 100), "Draining" if score >= 50 else ""


def detect_roundtrip(features: dict) -> tuple[float, str]:
    """
    패턴 4: Round-trip (순환 거래)
    - 총 거래 규모 대비 잔액 변화 거의 없음
    - 총 거래 건수 높음 (≥ 140)
    - 총 송신 ≈ 총 수신 (높은 수준에서)
    """
    score = 0.0

    total_sent = features.get("total Ether sent", 0)
    total_received = features.get("total ether received", 0)
    balance = features.get("total ether balance", 0)
    total_txn = features.get("total transactions (including tnx to create contract", 0)

    # 대규모 순환 (총 거래 건수 높으면서 잔액 변화 적음)
    if total_txn >= 140:
        # 총 송수신 대비 잔액 변화가 작음
        total_flow = total_sent + total_received
        if total_flow > 0 and abs(balance) < total_flow * 0.1:
            score += 40

        # 총 송신 ≈ 총 수신 (높은 수준에서)
        if total_sent > 50 and total_received > 50:
            flow_ratio = min(total_sent, total_received) / max(total_sent, total_received)
            if flow_ratio >= 0.6:
                score += 30

    # 중간 규모 순환
    elif total_txn >= 100:
        total_flow = total_sent + total_received
        if total_flow > 0 and abs(balance) < total_flow * 0.15:
            score += 25

    return min(score, 100), "Round-trip" if score >= 50 else ""


def detect_dust(features: dict) -> tuple[float, str]:
    """
    패턴 5: Dust Probing (소액 탐색)
    - 극소액 전송 (avg val sent ≤ 0.01)
    - 송신 건수 많음
    - 총 송신액 미미
    """
    score = 0.0

    avg_val_sent = features.get("avg val sent", 0)
    sent_tnx = features.get("Sent tnx", 0)
    total_sent = features.get("total Ether sent", 0)
    max_sent = features.get("max val sent", 0)

    # 극소액 전송 (Dust 핵심)
    if 0 < avg_val_sent <= 0.01:
        score += 35
    elif 0 < avg_val_sent <= 0.1:
        score += 15

    # 많은 건수 + 극소액 조합
    if sent_tnx >= 30 and avg_val_sent <= 0.1:
        score += 25
    elif sent_tnx >= 15 and avg_val_sent <= 0.05:
        score += 15

    # 총합 미미 (소액 탐색은 금액이 아닌 정보 수집 목적)
    if 0 < total_sent < 5.0 and sent_tnx >= 20:
        score += 20

    # max도 작음
    if 0 < max_sent <= 0.05:
        score += 15

    return min(score, 100), "Dust Probing" if score >= 50 else ""


def detect_pump_collect(features: dict) -> tuple[float, str]:
    """
    패턴 6: Pump & Collect (소액 수집 후 대량 인출)
    - 수신 건수 비정상 높음 (≥ 40)
    - 대량 단건 송신 (max val sent 높음)
    - 총 수신 ETH 높음
    """
    score = 0.0

    recv_tnx = features.get("Received Tnx", 0)
    max_val_sent = features.get("max val sent", 0)
    total_received = features.get("total ether received", 0)
    avg_val_recv = features.get("avg val received", 0)

    # 수신 건수 비정상 높음 (정상 max=26, 사기 min=55)
    if recv_tnx >= 50:
        score += 30
    elif recv_tnx >= 35:
        score += 15

    # 대량 단건 송신 존재
    if max_val_sent >= 10:
        score += 25
    elif max_val_sent >= 5:
        score += 10

    # 수신 소액 vs 송신 대량 비대칭
    if max_val_sent > 0 and avg_val_recv > 0:
        asymmetry = max_val_sent / avg_val_recv
        if asymmetry >= 5:
            score += 25
        elif asymmetry >= 3:
            score += 10

    # 총 수신 ETH 높음
    if total_received >= 100:
        score += 15

    return min(score, 100), "Pump&Collect" if score >= 50 else ""


# ═══════════════════════════════════════════════════════════════
# 통합 규칙 기반 판별
# ═══════════════════════════════════════════════════════════════

def rule_based_score(features: dict) -> dict:
    """
    모든 패턴 규칙을 실행하고 최종 점수를 반환한다.

    반환:
      {
        "rule_score": float (0~100),
        "detected_patterns": list[str],
        "pattern_scores": dict,
        "is_fraud": bool,
      }
    """
    detectors = [
        ("Smurfing", detect_smurfing),
        ("Layering", detect_layering),
        ("Draining", detect_draining),
        ("Round-trip", detect_roundtrip),
        ("Dust Probing", detect_dust),
        ("Pump&Collect", detect_pump_collect),
    ]

    pattern_scores = {}
    detected_patterns = []

    for name, detector in detectors:
        score, pattern = detector(features)
        pattern_scores[name] = score
        if pattern:
            detected_patterns.append(pattern)

    # 최종 점수 = 가장 높은 패턴 점수
    max_score = max(pattern_scores.values()) if pattern_scores else 0

    # 여러 패턴 동시 탐지 시 보너스 (복합 사기)
    if len(detected_patterns) >= 2:
        max_score = min(max_score + 10, 100)

    return {
        "rule_score": max_score,
        "detected_patterns": detected_patterns,
        "pattern_scores": pattern_scores,
        "is_fraud": max_score >= 50,
    }


def hybrid_score(features: dict, ai_proba: float, ai_weight: float = 0.3) -> dict:
    """
    AI 모델 + 규칙 기반 점수를 합산하여 최종 판별.

    현재 환경(로컬 Ganache)에서는 AI 모델의 feature 분포가
    학습 데이터(실제 이더리움)와 달라 정확도가 낮으므로
    규칙 기반에 더 높은 가중치를 부여한다.

    ai_weight: AI 비중 (기본 0.3 = 30%)
    rule_weight: 1 - ai_weight (기본 0.7 = 70%)
    """
    rule_result = rule_based_score(features)
    rule_weight = 1 - ai_weight

    # AI 확률이 음수(-1)면 서버 미연결 → 규칙만 사용
    if ai_proba < 0:
        final_score = rule_result["rule_score"]
    else:
        final_score = (ai_proba * ai_weight) + (rule_result["rule_score"] * rule_weight)

    return {
        "final_score": round(final_score, 2),
        "is_fraud": final_score >= 40,
        "ai_score": round(ai_proba, 2),
        "rule_score": round(rule_result["rule_score"], 2),
        "ai_weight": ai_weight,
        "rule_weight": rule_weight,
        "detected_patterns": rule_result["detected_patterns"],
        "pattern_scores": {k: round(v, 1) for k, v in rule_result["pattern_scores"].items()},
    }
