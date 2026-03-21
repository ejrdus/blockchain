"""
FDT 사기 탐지 대시보드 — Streamlit UI

기능:
  1. 계좌 목록 + ETH/FDT 잔액 조회
  2. 계좌 선택 시 거래 이력 & feature 분석
  3. 에스크로 송금 실행 (수신자 이력 경고 포함)
  4. 전체 거래 히스토리 조회
  5. 사기 패턴 설명 (논문용)

실행:
  streamlit run dashboard.py
"""

import streamlit as st
import json
import os
import sys
import requests
import pandas as pd
import joblib
import shap
import numpy as np
from datetime import datetime, timezone

from web3 import Web3

# ── 경로 설정 ──
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "A_blockchain"))
sys.path.append(os.path.join(ROOT_DIR, "C_smart_contract"))

from config import GANACHE_URL, FDS_SERVER_URL, FDS_ENDPOINT
from config import IS_LOCAL_GANACHE, GANACHE_SCALE_FACTORS
from A_blockchain.read_block import analyze_address
from C_smart_contract.rule_engine import rule_based_score, hybrid_score


def scale_ganache_features(features: dict) -> dict:
    """
    Ganache feature를 실제 이더리움 스케일로 변환 (Kaggle 모델 대응)
    각 feature: result = base + value * scale
    - base : 시간 feature 등 Ganache에서 ≈0인 값에 최소 기반값 부여
    - scale: 곱셈 스케일 팩터로 계좌 간 차이 증폭
    """
    if not IS_LOCAL_GANACHE:
        return features
    scaled = {}
    for k, v in features.items():
        config = GANACHE_SCALE_FACTORS.get(k)
        if config is None:
            scaled[k] = v
        else:
            base, scale = config
            # 원본값이 0이면 base만, 0보다 크면 base + 증폭된 차이
            scaled[k] = base + v * scale
    return scaled

DEPLOY_INFO_PATH = os.path.join(ROOT_DIR, "C_smart_contract", "deploy_info.json")
ABI_PATH = os.path.join(ROOT_DIR, "C_smart_contract", "abi", "Token.json")

ACCOUNT_LABELS = [
    "배포자/Owner", "정상 A", "정상 B", "정상 C", "정상 D",
    "사기 S1 (Smurfing)", "사기 S2 (Layering)", "사기 S3 (Dust/Collect)",
    "중립 E", "중립 F"
]


# ═══════════════════════════════════════════════════════════════
# 연결 & 로드
# ═══════════════════════════════════════════════════════════════

SHAP_CSV_PATH = os.path.join(ROOT_DIR, "shap_results.csv")


@st.cache_resource
def load_fraud_model():
    """SHAP 계산용 실 이더리움 모델 로드"""
    model_path = os.path.join(ROOT_DIR, "B_ai_fds", "fraud_model_artifact.pkl")
    return joblib.load(model_path)


@st.cache_resource
def get_shap_explainer():
    """TreeExplainer 캐싱 (느린 초기화 1회만)"""
    artifact = load_fraud_model()
    return shap.TreeExplainer(artifact["model"]), artifact["feature_cols"]


def compute_and_save_shap(features: dict, sender: str, receiver: str,
                           amount: float, result: dict) -> pd.DataFrame | None:
    """
    SHAP 값을 계산하고 shap_results.csv에 누적 저장한다.
    반환: SHAP 값 DataFrame (UI 표시용), 실패 시 None
    """
    try:
        explainer, feature_cols = get_shap_explainer()
        X = pd.DataFrame([{col: features.get(col, 0) for col in feature_cols}])

        raw = explainer.shap_values(X)
        # LightGBM 이진 분류: list[2] 또는 단일 array 모두 처리
        if isinstance(raw, list):
            sv = np.array(raw[1][0])
        else:
            sv = np.array(raw[0])

        # ── CSV 행 구성 ──
        row = {
            "timestamp":         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "sender":            sender,
            "receiver":          receiver,
            "amount_fdt":        amount,
            "fraud_probability": result["final_score"],
            "threshold":         result["threshold"],
            "verdict":           "차단" if result["is_fraud"] else "승인",
        }
        for col, val in zip(feature_cols, sv):
            row[f"shap_{col}"] = round(float(val), 6)

        df_row = pd.DataFrame([row])
        if os.path.exists(SHAP_CSV_PATH):
            df_row.to_csv(SHAP_CSV_PATH, mode="a", header=False, index=False, encoding="utf-8-sig")
        else:
            df_row.to_csv(SHAP_CSV_PATH, mode="w", header=True,  index=False, encoding="utf-8-sig")

        # UI 표시용: feature별 SHAP 절댓값 기준 정렬
        shap_df = pd.DataFrame({
            "Feature":    feature_cols,
            "SHAP 기여값": [round(float(v), 4) for v in sv],
            "|기여값|":    [round(abs(float(v)), 4) for v in sv],
        }).sort_values("|기여값|", ascending=False).drop(columns=["|기여값|"])

        return shap_df

    except Exception as e:
        st.warning(f"SHAP 계산 실패: {e}")
        return None


@st.cache_resource
def connect():
    """Ganache 연결 + 컨트랙트 로드"""
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    if not w3.is_connected():
        st.error("Ganache 연결 실패. Ganache가 실행 중인지 확인하세요.")
        st.stop()

    if not os.path.exists(DEPLOY_INFO_PATH):
        st.error("deploy_info.json이 없습니다. deploy.py를 먼저 실행하세요.")
        st.stop()

    with open(DEPLOY_INFO_PATH, "r") as f:
        deploy_info = json.load(f)
    with open(ABI_PATH, "r") as f:
        abi = json.load(f)

    contract = w3.eth.contract(
        address=deploy_info["contract_address"], abi=abi
    )
    return w3, contract, deploy_info


def get_account_info(w3, contract, address, idx):
    """계좌 정보 조회"""
    eth_bal = float(w3.from_wei(w3.eth.get_balance(address), "ether"))
    fdt_bal = contract.functions.balanceOf(address).call() / (10 ** 18)
    label = ACCOUNT_LABELS[idx] if idx < len(ACCOUNT_LABELS) else f"계좌 {idx}"
    return {
        "index": idx,
        "label": label,
        "address": address,
        "eth_balance": eth_bal,
        "fdt_balance": fdt_bal,
    }


def get_tx_history(w3, address):
    """특정 주소의 모든 거래 내역 조회"""
    target = w3.to_checksum_address(address)
    txs = []
    latest = w3.eth.block_number

    for num in range(0, latest + 1):
        block = w3.eth.get_block(num, full_transactions=True)
        for tx in block.transactions:
            if tx["from"] == target or tx["to"] == target:
                direction = "송신" if tx["from"] == target else "수신"
                counterparty = tx["to"] if direction == "송신" else tx["from"]
                val = float(w3.from_wei(tx["value"], "ether"))
                txs.append({
                    "블록": block.number,
                    "방향": direction,
                    "상대방": counterparty[:14] + "..." if counterparty else "컨트랙트 생성",
                    "금액 (ETH)": round(val, 6),
                    "TX Hash": tx.hash.hex()[:16] + "...",
                    "시각": datetime.fromtimestamp(
                        block.timestamp, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                })
    return txs


def check_receiver_history(w3, address):
    """수신자 거래 이력 확인"""
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
        return "danger", sent_count, recv_count
    elif total <= 2:
        return "caution", sent_count, recv_count
    else:
        return "safe", sent_count, recv_count


def run_ai_check(w3, address):
    """A파트 feature 추출 + B파트 AI 판별 (AI 기반, 임계값 32%)"""
    try:
        features = analyze_address(w3, address)
    except Exception as e:
        return None, None, None, None, str(e)

    # Ganache → 실 이더리움 스케일 변환
    scaled_features = scale_ganache_features(features)

    # AI 모델 호출
    ai_proba = -1.0
    ai_result = None
    try:
        res = requests.post(
            FDS_SERVER_URL + FDS_ENDPOINT,
            json={"features": scaled_features},
            timeout=10,
        )
        ai_result = res.json()
        ai_proba = ai_result["pred_proba"]
    except Exception:
        pass  # AI 서버 미연결 → 규칙 fallback

    # 참고용 패턴 정보
    rule_info = rule_based_score(features)

    # AI 기반 판별 (임계값 32%)
    THRESHOLD = 32.0
    if ai_proba >= 0:
        final_score = ai_proba
        is_fraud = ai_proba >= THRESHOLD
    else:
        # AI 서버 미연결 → 규칙 기반 fallback
        final_score = rule_info["rule_score"]
        is_fraud = final_score >= THRESHOLD

    result = {
        "final_score": round(final_score, 2),
        "is_fraud": is_fraud,
        "ai_score": round(ai_proba, 2),
        "threshold": THRESHOLD,
        "detected_patterns": rule_info["detected_patterns"],
        "pattern_scores": {k: round(v, 1) for k, v in rule_info["pattern_scores"].items()},
        "ai_connected": ai_proba >= 0,
    }

    return features, scaled_features, ai_result, result, None


def execute_escrow(w3, contract, sender, receiver, amount_fdt):
    """에스크로 전체 흐름 실행 (UI용)"""
    decimals = contract.functions.decimals().call()
    raw_amount = int(amount_fdt * (10 ** decimals))
    owner = w3.eth.accounts[0]

    # 1. 예치
    tx = contract.functions.escrowDeposit(receiver, raw_amount).build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    logs = contract.events.EscrowDeposited().process_receipt(receipt)
    tx_id = logs[0]["args"]["txId"]

    return tx_id, receipt.blockNumber


def approve_escrow(w3, contract, tx_id):
    """에스크로 승인"""
    owner = w3.eth.accounts[0]
    tx = contract.functions.escrowApprove(tx_id).build_transaction({
        "from": owner,
        "nonce": w3.eth.get_transaction_count(owner),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    w3.eth.wait_for_transaction_receipt(tx_hash)


def reject_escrow(w3, contract, tx_id):
    """에스크로 거부"""
    owner = w3.eth.accounts[0]
    tx = contract.functions.escrowReject(tx_id).build_transaction({
        "from": owner,
        "nonce": w3.eth.get_transaction_count(owner),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_transaction(tx)
    w3.eth.wait_for_transaction_receipt(tx_hash)


# ═══════════════════════════════════════════════════════════════
# Streamlit UI
# ═══════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="FDT 사기 탐지 시스템",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ FDT 블록체인 사기 탐지 시스템")
st.caption("FraudDetectionToken — 에스크로 기반 AI 사기 탐지 (Kaggle 이더리움 모델)")

w3, contract, deploy_info = connect()
accounts = w3.eth.accounts

# ── 사이드바: 시스템 상태 ──
with st.sidebar:
    st.header("시스템 상태")

    st.success(f"Ganache 연결됨")
    st.info(f"컨트랙트: {deploy_info['contract_address'][:16]}...")
    st.info(f"블록 수: {w3.eth.block_number + 1}")

    # FDS 서버 상태
    try:
        health = requests.get(FDS_SERVER_URL + "/health", timeout=3).json()
        st.success(f"FDS 서버: {health['model_name']}")
    except Exception:
        st.warning("FDS 서버 미연결")

    st.divider()
    st.header("사기 패턴 범례")
    st.markdown("""
    - 🟢 **정상**: 소액 분산, 양방향
    - 🔴 **Smurfing**: 소액 반복 세탁
    - 🔴 **Layering**: 즉시 전달 세탁
    - 🔴 **Draining**: 전액 인출
    - 🔴 **Round-trip**: 순환 거래
    - 🔴 **Dust**: 극소액 탐색
    - 🔴 **Pump&Collect**: 수집 후 인출
    - 🟡 **중립**: 혼합 패턴
    """)


# ── 탭 구성 ──
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 계좌 현황", "💸 송금 실행", "🔍 계좌 분석", "📜 거래 히스토리"
])


# ═══════════════════════════════════════════════════════════════
# 탭 1: 계좌 현황
# ═══════════════════════════════════════════════════════════════
with tab1:
    st.header("계좌 목록 & 잔액")

    account_data = []
    for i, addr in enumerate(accounts):
        info = get_account_info(w3, contract, addr, i)
        account_data.append({
            "#": i,
            "역할": info["label"],
            "주소": addr[:18] + "...",
            "ETH 잔액": f"{info['eth_balance']:,.2f}",
            "FDT 잔액": f"{info['fdt_balance']:,.0f}",
        })

    df = pd.DataFrame(account_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 토큰 기본 정보
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("토큰 이름", contract.functions.name().call())
    with col2:
        st.metric("심볼", contract.functions.symbol().call())
    with col3:
        total = contract.functions.totalSupply().call() / (10 ** 18)
        st.metric("총 발행량", f"{total:,.0f} FDT")


# ═══════════════════════════════════════════════════════════════
# 탭 2: 송금 실행
# ═══════════════════════════════════════════════════════════════
with tab2:
    st.header("에스크로 송금")

    col_send, col_recv = st.columns(2)

    with col_send:
        sender_idx = st.selectbox(
            "송신자 선택",
            range(len(accounts)),
            format_func=lambda i: f"[{i}] {ACCOUNT_LABELS[i]} — {accounts[i][:14]}...",
            key="sender",
        )

    with col_recv:
        receiver_idx = st.selectbox(
            "수신자 선택",
            range(len(accounts)),
            format_func=lambda i: f"[{i}] {ACCOUNT_LABELS[i]} — {accounts[i][:14]}...",
            key="receiver",
            index=1,
        )

    amount = st.number_input("송금액 (FDT)", min_value=1.0, max_value=100000.0, value=100.0, step=10.0)

    # 수신자 잔액 미리보기
    sender_fdt = contract.functions.balanceOf(accounts[sender_idx]).call() / (10 ** 18)
    st.caption(f"송신자 FDT 잔액: {sender_fdt:,.0f} FDT")

    if sender_idx == receiver_idx:
        st.warning("송신자와 수신자가 같습니다.")

    if st.button("🚀 송금 실행", type="primary", disabled=(sender_idx == receiver_idx)):
        sender_addr = accounts[sender_idx]
        receiver_addr = accounts[receiver_idx]

        with st.spinner("수신자 이력 확인 중..."):
            warning_level, s_cnt, r_cnt = check_receiver_history(w3, receiver_addr)

        # ── 경고 표시 ──
        if warning_level == "danger":
            st.error(
                f"⚠️ **경고: 수신자 거래 이력 없음!**\n\n"
                f"수신자 `{receiver_addr[:14]}...`의 거래 이력이 전혀 없습니다.\n"
                f"AI 사기 검증을 수행할 수 없어 **송금이 보류(반환)** 처리됩니다.\n\n"
                f"수신자에게 최소 1건 이상의 거래 이력이 있어야 검증이 가능합니다."
            )

            with st.spinner("에스크로 예치 후 반환 처리 중..."):
                tx_id, block_num = execute_escrow(w3, contract, sender_addr, receiver_addr, amount)
                reject_escrow(w3, contract, tx_id)

            st.warning(f"↩️ {amount:,.0f} FDT가 송신자에게 반환되었습니다. (EscrowTx #{tx_id})")

        else:
            if warning_level == "caution":
                st.warning(
                    f"⚠️ **주의: 수신자 거래 이력 부족**\n\n"
                    f"수신자 이력: 송신 {s_cnt}건 / 수신 {r_cnt}건\n"
                    f"AI 정확도가 낮을 수 있습니다."
                )

            # 에스크로 예치
            with st.spinner("에스크로 예치 중..."):
                tx_id, block_num = execute_escrow(w3, contract, sender_addr, receiver_addr, amount)
            st.info(f"예치 완료 — EscrowTx #{tx_id} (블록 {block_num})")

            # AI 검증 (100% AI)
            with st.spinner("AI 사기 검증 중..."):
                features, scaled_features, ai_result, result, error = run_ai_check(w3, receiver_addr)

            if error:
                st.warning(f"검증 실패: {error} — 검증 없이 승인 처리")
                approve_escrow(w3, contract, tx_id)
                st.success(f"✅ {amount:,.0f} FDT 전송 완료! → {receiver_addr[:14]}...")
            else:
                # 결과 표시
                col_r1, col_r2, col_r3 = st.columns(3)
                with col_r1:
                    st.metric("사기 확률", f"{result['final_score']}%")
                with col_r2:
                    ai_display = f"{result['ai_score']}%" if result['ai_connected'] else "미연결 (규칙 fallback)"
                    st.metric("AI 모델", ai_display)
                with col_r3:
                    st.metric("판별 임계값", f"{result['threshold']}%")

                # 판별 결과
                is_fraud = result["is_fraud"]
                threshold_pct = result["threshold"]
                final_score = result["final_score"]
                # 임계값 ±30% 범위를 caution zone으로 정의
                caution_margin = threshold_pct * 0.3
                is_near_threshold = abs(final_score - threshold_pct) <= caution_margin

                if result["detected_patterns"]:
                    st.info(f"참고 패턴: **{', '.join(result['detected_patterns'])}**")

                # ── 임계값 근처 안내창 ──
                if is_near_threshold:
                    if not is_fraud:
                        st.warning(
                            f"⚠️ **주의: 사기 확률({final_score}%)이 차단 기준({threshold_pct}%)에 근접합니다.**\n\n"
                            f"사기일 가능성이 있으니 수신자를 **한 번 더 확인**해 보세요."
                        )
                    else:
                        st.warning(
                            f"⚠️ **참고: 사기 확률({final_score}%)이 차단 기준({threshold_pct}%) 근처입니다.**\n\n"
                            f"확실한 사기가 아닐 수 있으니 수신자 정보를 직접 확인 후 재시도를 고려하세요."
                        )

                if not is_fraud:
                    approve_escrow(w3, contract, tx_id)
                    st.success(f"✅ 전송 완료! {amount:,.0f} FDT → [{receiver_idx}] {ACCOUNT_LABELS[receiver_idx]}")
                else:
                    reject_escrow(w3, contract, tx_id)
                    st.error(
                        f"🚨 사기 의심 (사기 확률 {result['final_score']}%) — 송금 차단!\n\n"
                        f"참고 패턴: {', '.join(result['detected_patterns'])}\n\n"
                        f"{amount:,.0f} FDT가 송신자에게 반환되었습니다."
                    )

                # ── SHAP 계산 & CSV 저장 ──
                if scaled_features:
                    with st.spinner("SHAP 분석 중..."):
                        shap_df = compute_and_save_shap(
                            scaled_features,
                            sender=sender_addr,
                            receiver=receiver_addr,
                            amount=amount,
                            result=result,
                        )

                    if shap_df is not None:
                        st.success(f"📄 SHAP 결과 저장 완료 → `shap_results.csv`")
                        with st.expander("📊 SHAP 기여값 (상위 15개 feature)", expanded=False):
                            st.dataframe(
                                shap_df.head(15),
                                use_container_width=True,
                                hide_index=True,
                            )
                            st.caption(
                                "양수(+): 사기 확률 상승에 기여 / 음수(−): 사기 확률 하락에 기여"
                            )

                # 패턴별 점수 상세
                if features:
                    with st.expander("참고: 패턴별 규칙 점수"):
                        pattern_df = pd.DataFrame(
                            list(result["pattern_scores"].items()),
                            columns=["패턴", "점수"]
                        ).sort_values("점수", ascending=False)
                        st.dataframe(pattern_df, use_container_width=True, hide_index=True)

                    with st.expander("주요 Feature 상세"):
                        important_features = {
                            "Sent tnx (송신 건수)": features.get("Sent tnx", 0),
                            "Received Tnx (수신 건수)": features.get("Received Tnx", 0),
                            "Unique Sent To Addresses": features.get("Unique Sent To Addresses", 0),
                            "Unique Received From Addresses": features.get("Unique Received From Addresses", 0),
                            "avg val sent": round(features.get("avg val sent", 0), 6),
                            "avg val received": round(features.get("avg val received", 0), 6),
                            "total Ether sent": round(features.get("total Ether sent", 0), 4),
                            "total ether received": round(features.get("total ether received", 0), 4),
                            "total ether balance": round(features.get("total ether balance", 0), 4),
                            "sent_received_ratio": round(features.get("sent_received_ratio", 0), 4),
                            "min val sent": round(features.get("min val sent", 0), 6),
                            "max val sent": round(features.get("max val sent", 0), 6),
                        }
                        st.json(important_features)


# ═══════════════════════════════════════════════════════════════
# 탭 3: 계좌 분석
# ═══════════════════════════════════════════════════════════════
with tab3:
    st.header("계좌 상세 분석")

    analysis_idx = st.selectbox(
        "분석할 계좌 선택",
        range(len(accounts)),
        format_func=lambda i: f"[{i}] {ACCOUNT_LABELS[i]} — {accounts[i][:14]}...",
        key="analysis",
    )

    if st.button("🔍 분석 실행"):
        addr = accounts[analysis_idx]

        # 이력 확인
        with st.spinner("거래 이력 확인 중..."):
            warning_level, s_cnt, r_cnt = check_receiver_history(w3, addr)

        col_h1, col_h2, col_h3 = st.columns(3)
        with col_h1:
            st.metric("송신 건수", s_cnt)
        with col_h2:
            st.metric("수신 건수", r_cnt)
        with col_h3:
            if warning_level == "danger":
                st.metric("이력 상태", "❌ 이력 없음")
            elif warning_level == "caution":
                st.metric("이력 상태", "⚠️ 부족")
            else:
                st.metric("이력 상태", "✅ 충분")

        if warning_level == "danger":
            st.error("거래 이력이 없어 AI 분석을 수행할 수 없습니다.")
        else:
            # AI 분석 (100% AI)
            with st.spinner("AI 분석 중..."):
                features, scaled_features, ai_result, result, error = run_ai_check(w3, addr)

            if error:
                st.warning(f"분석 실패: {error}")
            else:
                mode_label = "AI 기반" if result["ai_connected"] else "규칙 기반 (AI 미연결)"
                st.subheader(f"판별 결과 — {mode_label}")
                col_a1, col_a2, col_a3 = st.columns(3)
                with col_a1:
                    st.metric("사기 확률", f"{result['final_score']}%")
                with col_a2:
                    ai_display = f"{result['ai_score']}%" if result['ai_connected'] else "미연결"
                    st.metric("AI 모델", ai_display)
                with col_a3:
                    st.metric("판별 임계값", f"{result['threshold']}%")

                threshold_pct = result["threshold"]
                final_score = result["final_score"]
                caution_margin = threshold_pct * 0.3
                is_near_threshold = abs(final_score - threshold_pct) <= caution_margin

                if result["is_fraud"]:
                    st.error(f"🚨 사기 의심 — 참고 패턴: {', '.join(result['detected_patterns'])}")
                    if is_near_threshold:
                        st.warning(
                            f"⚠️ 사기 확률({final_score}%)이 차단 기준({threshold_pct}%) 근처입니다. "
                            f"불확실한 경우이니 수신자를 직접 확인해 보세요."
                        )
                else:
                    if is_near_threshold:
                        st.warning(
                            f"⚠️ **주의:** 사기 확률 **{final_score}%** 이 차단 기준 **{threshold_pct}%** 에 근접합니다. "
                            f"사기일 가능성이 있으니 한 번 더 확인해 보세요."
                        )
                    st.success("✅ 정상 계좌로 판별")

                # 패턴별 점수 (참고)
                st.subheader("참고: 패턴별 규칙 점수")
                pattern_df = pd.DataFrame(
                    list(result["pattern_scores"].items()),
                    columns=["패턴", "점수"]
                ).sort_values("점수", ascending=False)
                st.dataframe(pattern_df, use_container_width=True, hide_index=True)

                # Feature 시각화
                st.subheader("주요 Feature")

                feature_display = {
                    "거래 패턴": {
                        "송신 건수 (Sent tnx)": features.get("Sent tnx", 0),
                        "수신 건수 (Received Tnx)": features.get("Received Tnx", 0),
                        "총 거래 수": features.get("total transactions (including tnx to create contract", 0),
                        "고유 송신 상대 수": features.get("Unique Sent To Addresses", 0),
                        "고유 수신 상대 수": features.get("Unique Received From Addresses", 0),
                    },
                    "금액 통계": {
                        "총 송신 ETH": round(features.get("total Ether sent", 0), 4),
                        "총 수신 ETH": round(features.get("total ether received", 0), 4),
                        "잔액": round(features.get("total ether balance", 0), 4),
                        "평균 송신액": round(features.get("avg val sent", 0), 6),
                        "평균 수신액": round(features.get("avg val received", 0), 6),
                        "최소 송신액": round(features.get("min val sent", 0), 6),
                        "최대 송신액": round(features.get("max val sent", 0), 6),
                    },
                    "파생 지표": {
                        "송수신 비율": round(features.get("sent_received_ratio", 0), 4),
                        "고유 상대방 비율": round(features.get("unique_counterparty_ratio", 0), 4),
                    },
                }

                for section, data in feature_display.items():
                    with st.expander(section, expanded=True):
                        df_feat = pd.DataFrame(
                            list(data.items()),
                            columns=["Feature", "값"]
                        )
                        st.dataframe(df_feat, use_container_width=True, hide_index=True)

                # 전체 feature JSON
                with st.expander("전체 Feature (Raw JSON)"):
                    st.json(features)


# ═══════════════════════════════════════════════════════════════
# 탭 4: 거래 히스토리
# ═══════════════════════════════════════════════════════════════
with tab4:
    st.header("거래 히스토리")

    history_idx = st.selectbox(
        "조회할 계좌 선택",
        range(len(accounts)),
        format_func=lambda i: f"[{i}] {ACCOUNT_LABELS[i]} — {accounts[i][:14]}...",
        key="history",
    )

    if st.button("📜 이력 조회"):
        with st.spinner("거래 내역 조회 중..."):
            txs = get_tx_history(w3, accounts[history_idx])

        if not txs:
            st.info("거래 내역이 없습니다.")
        else:
            st.success(f"총 {len(txs)}건의 거래 발견")

            # 송신/수신 통계
            sent = [t for t in txs if t["방향"] == "송신"]
            recv = [t for t in txs if t["방향"] == "수신"]

            col_s1, col_s2, col_s3 = st.columns(3)
            with col_s1:
                st.metric("총 거래", f"{len(txs)}건")
            with col_s2:
                st.metric("송신", f"{len(sent)}건")
            with col_s3:
                st.metric("수신", f"{len(recv)}건")

            df_tx = pd.DataFrame(txs)
            st.dataframe(df_tx, use_container_width=True, hide_index=True)
