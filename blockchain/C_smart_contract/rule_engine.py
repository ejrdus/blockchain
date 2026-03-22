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
│  1. Smurfing    : 송신 多 + 건당 소액 + 수신자 多          │
│  2. Layering    : 수신≈송신 + 잔액≈0 + 빠른 전달          │
│  3. Draining    : 대량 단건 송신 + 수신자 1~2개            │
│  4. Round-trip  : 송신≈수신 + 잔액 변화 없음              │
│  5. Dust        : 극소액 多건 전송                         │
│  6. Pump&Collect: 소액 多수신 + 대량 단건 송신             │
└──────────────────────────────────────────────────────────┘
"""


def detect_smurfing(features: dict) -> tuple[float, str]:
    """
    패턴 1: Smurfing (소액 분산 세탁)
    - 송신 건수 많음 (≥ 10)
    - 건당 평균 송신액 작음 (≤ 1.0)
    - 고유 수신 주소 많음 (≥ 3)
    - 총 송신액은 큼
    """
    score = 0.0
    reasons = []

    sent_tnx = features.get("Sent tnx", 0)
    avg_val_sent = features.get("avg val sent", 0)
    unique_sent_to = features.get("Unique Sent To Addresses", 0)
    total_sent = features.get("total Ether sent", 0)

    if sent_tnx >= 10:
        score += 30
        reasons.append(f"송신 건수 높음({sent_tnx}건)")
    elif sent_tnx >= 5:
        score += 15
        reasons.append(f"송신 건수 다소 높음({sent_tnx}건)")

    if 0 < avg_val_sent <= 1.0:
        score += 25
        reasons.append(f"건당 소액 전송({avg_val_sent:.4f} ETH)")
    elif 0 < avg_val_sent <= 2.0:
        score += 10

    if unique_sent_to >= 4:
        score += 20
        reasons.append(f"다수 수신자({unique_sent_to}개 주소)")
    elif unique_sent_to >= 2:
        score += 10

    if total_sent > 5 and avg_val_sent <= 1.0:
        score += 15
        reasons.append(f"소액 분산이지만 총합 큼({total_sent:.2f} ETH)")

    # min/max 차이가 작으면 (금액 균일)
    min_sent = features.get("min val sent", 0)
    max_sent = features.get("max val sent", 0)
    if max_sent > 0 and min_sent > 0:
        ratio = min_sent / max_sent
        if ratio > 0.3 and sent_tnx >= 5:
            score += 10
            reasons.append("금액 균일성 높음")

    return min(score, 100), "Smurfing" if score >= 40 else ""


def detect_layering(features: dict) -> tuple[float, str]:
    """
    패턴 2: Layering (다단계 세탁)
    - 총 수신 ≈ 총 송신 (sent_received_ratio ≈ 1.0)
    - 잔액 ≈ 0
    - 수신 건수와 송신 건수 모두 있음
    """
    score = 0.0
    reasons = []

    total_sent = features.get("total Ether sent", 0)
    total_received = features.get("total ether received", 0)
    balance = features.get("total ether balance", 0)
    sent_recv_ratio = features.get("sent_received_ratio", 0)
    sent_tnx = features.get("Sent tnx", 0)
    recv_tnx = features.get("Received Tnx", 0)

    # 보낸 만큼 받음 (중개 역할)
    if total_sent > 0 and total_received > 0:
        if 0.5 <= sent_recv_ratio <= 2.0:
            score += 25
            reasons.append(f"송수신 비율 ≈ 1 (ratio={sent_recv_ratio:.2f})")

    # 잔액 거의 없음 (다 전달함)
    if total_received > 0 and abs(balance) < total_received * 0.2:
        score += 25
        reasons.append(f"잔액 거의 없음({balance:.2f} ETH)")

    # 수신도 하고 송신도 함 (중개)
    if sent_tnx >= 3 and recv_tnx >= 2:
        score += 20
        reasons.append(f"수신({recv_tnx}건)+송신({sent_tnx}건) 모두 활발")

    # 다수 수신자에게 분산 전달
    unique_sent_to = features.get("Unique Sent To Addresses", 0)
    if unique_sent_to >= 3:
        score += 15
        reasons.append(f"다수에게 분산 전달({unique_sent_to}곳)")

    # 다수 출처에서 수신
    unique_recv_from = features.get("Unique Received From Addresses", 0)
    if unique_recv_from >= 2:
        score += 15
        reasons.append(f"다수 출처에서 수신({unique_recv_from}곳)")

    return min(score, 100), "Layering" if score >= 40 else ""


def detect_draining(features: dict) -> tuple[float, str]:
    """
    패턴 3: Account Draining (전액 인출)
    - 대량 단건 송신 (max val sent 높음)
    - 송신 건수 적음 (1~3건)
    - 수신자 1~2개
    - 잔액 → 0
    """
    score = 0.0
    reasons = []

    max_val_sent = features.get("max val sent", 0)
    sent_tnx = features.get("Sent tnx", 0)
    unique_sent_to = features.get("Unique Sent To Addresses", 0)
    balance = features.get("total ether balance", 0)
    total_received = features.get("total ether received", 0)
    recv_tnx = features.get("Received Tnx", 0)

    # 대량 송신
    if max_val_sent >= 8:
        score += 30
        reasons.append(f"대량 송신({max_val_sent:.2f} ETH)")
    elif max_val_sent >= 5:
        score += 15

    # 송신 건수 적음 + 수신자 적음 = 빼돌리기
    if 1 <= sent_tnx <= 3 and unique_sent_to <= 2:
        score += 25
        reasons.append(f"적은 건수({sent_tnx})로 소수({unique_sent_to})에게 집중")

    # 잔액 거의 0
    if total_received > 0 and balance < total_received * 0.1:
        score += 20
        reasons.append("잔액 거의 소진")

    # 이전에 수신 이력은 있음 (탈취 전 정상 사용)
    if recv_tnx >= 3 and sent_tnx <= 3:
        score += 15
        reasons.append("수신 이력 풍부하지만 송신은 소수")

    return min(score, 100), "Draining" if score >= 40 else ""


def detect_roundtrip(features: dict) -> tuple[float, str]:
    """
    패턴 4: Round-trip (순환 거래)
    - 송신 ≈ 수신 건수
    - 잔액 거의 변화 없음
    - 동일 금액 반복 (min ≈ max ≈ avg)
    """
    score = 0.0
    reasons = []

    sent_tnx = features.get("Sent tnx", 0)
    recv_tnx = features.get("Received Tnx", 0)
    min_sent = features.get("min val sent", 0)
    max_sent = features.get("max val sent", 0)
    avg_sent = features.get("avg val sent", 0)
    total_sent = features.get("total Ether sent", 0)
    total_received = features.get("total ether received", 0)

    # 송신 ≈ 수신 건수
    if sent_tnx >= 3 and recv_tnx >= 3:
        ratio = min(sent_tnx, recv_tnx) / max(sent_tnx, recv_tnx)
        if ratio >= 0.6:
            score += 25
            reasons.append(f"송신({sent_tnx})≈수신({recv_tnx}) 균형")

    # 동일 금액 반복
    if max_sent > 0 and min_sent > 0:
        uniformity = min_sent / max_sent
        if uniformity >= 0.5 and sent_tnx >= 3:
            score += 25
            reasons.append(f"금액 균일성 높음(min/max={uniformity:.2f})")

    # 총 송신 ≈ 총 수신 (순환)
    if total_sent > 0 and total_received > 0:
        flow_ratio = min(total_sent, total_received) / max(total_sent, total_received)
        if flow_ratio >= 0.5:
            score += 20
            reasons.append(f"총 송수신 균형(ratio={flow_ratio:.2f})")

    # 잔액 변화 거의 없음
    balance = features.get("total ether balance", 0)
    if total_received > 0 and abs(balance) < total_received * 0.3:
        score += 15

    return min(score, 100), "Round-trip" if score >= 40 else ""


def detect_dust(features: dict) -> tuple[float, str]:
    """
    패턴 5: Dust Probing (소액 탐색)
    - 극소액 전송 (avg val sent ≤ 0.01)
    - 송신 건수 많음
    - 총 송신액 미미
    """
    score = 0.0
    reasons = []

    avg_val_sent = features.get("avg val sent", 0)
    sent_tnx = features.get("Sent tnx", 0)
    total_sent = features.get("total Ether sent", 0)
    max_sent = features.get("max val sent", 0)
    unique_sent_to = features.get("Unique Sent To Addresses", 0)

    # 극소액
    if 0 < avg_val_sent <= 0.01:
        score += 35
        reasons.append(f"극소액 전송(avg={avg_val_sent:.6f} ETH)")
    elif 0 < avg_val_sent <= 0.1:
        score += 15

    # 많은 건수
    if sent_tnx >= 8:
        score += 25
        reasons.append(f"다수 전송({sent_tnx}건)")
    elif sent_tnx >= 5:
        score += 15

    # 총합 미미
    if 0 < total_sent < 1.0 and sent_tnx >= 5:
        score += 20
        reasons.append(f"총합 미미({total_sent:.4f} ETH)")

    # max도 작음
    if 0 < max_sent <= 0.01:
        score += 15
        reasons.append("최대 송신액도 극소")

    # 다수 주소에 전송
    if unique_sent_to >= 3:
        score += 10

    return min(score, 100), "Dust Probing" if score >= 40 else ""


def detect_pump_collect(features: dict) -> tuple[float, str]:
    """
    패턴 6: Pump & Collect (소액 수집 후 대량 인출)
    - 수신 건수 많음 + 수신 출처 다양
    - 이후 대량 단건 송신
    - 수신액 소액 + 송신액 대량
    """
    score = 0.0
    reasons = []

    recv_tnx = features.get("Received Tnx", 0)
    unique_recv_from = features.get("Unique Received From Addresses", 0)
    avg_val_recv = features.get("avg val received", 0)
    max_val_sent = features.get("max val sent", 0)
    sent_tnx = features.get("Sent tnx", 0)
    total_received = features.get("total ether received", 0)

    # 다수에서 수신
    if recv_tnx >= 5:
        score += 20
        reasons.append(f"다수 수신({recv_tnx}건)")
    elif recv_tnx >= 3:
        score += 10

    # 다양한 출처
    if unique_recv_from >= 3:
        score += 20
        reasons.append(f"다양한 출처({unique_recv_from}곳)")

    # 수신은 소액인데 송신은 대량 (비대칭)
    if max_val_sent > 0 and avg_val_recv > 0:
        asymmetry = max_val_sent / avg_val_recv
        if asymmetry >= 5:
            score += 30
            reasons.append(f"수신 소액 vs 송신 대량(비대칭 {asymmetry:.1f}배)")
        elif asymmetry >= 3:
            score += 15

    # 송신 건수 적음 (한 번에 인출)
    if sent_tnx <= 3 and max_val_sent >= 5:
        score += 15
        reasons.append("적은 건수로 대량 인출")

    return min(score, 100), "Pump&Collect" if score >= 40 else ""


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
        "details": list[str],
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
    all_reasons = []

    for name, detector in detectors:
        score, pattern = detector(features)
        pattern_scores[name] = score
        if pattern:
            detected_patterns.append(pattern)

    # 최종 점수 = 가장 높은 패턴 점수
    max_score = max(pattern_scores.values()) if pattern_scores else 0

    # 여러 패턴 동시 탐지 시 보너스 (복합 사기)
    if len(detected_patterns) >= 2:
        max_score = min(max_score + 15, 100)

    return {
        "rule_score": max_score,
        "detected_patterns": detected_patterns,
        "pattern_scores": pattern_scores,
        "is_fraud": max_score >= 40,
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
