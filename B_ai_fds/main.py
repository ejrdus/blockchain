import joblib
import os

# 어디서 실행하든 main.py 기준으로 pkl 파일을 찾도록 절대 경로 사용
_dir = os.path.dirname(os.path.abspath(__file__))
artifact = joblib.load(os.path.join(_dir, "fraud_model_artifact.pkl"))

model = artifact["model"]
feature_cols = artifact["feature_cols"]
threshold = artifact["threshold"]
model_name = artifact["model_name"]

import pandas as pd

def predict_from_features(feature_dict):
    X_new = pd.DataFrame([feature_dict], columns=feature_cols)
    proba = float(model.predict_proba(X_new)[:, 1][0])
    return { "pred_label": int(proba >= threshold), "pred_proba": round(proba*100, 4), "threshold": round(threshold*100, 1) }

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class PredictRequest(BaseModel):
    features: dict

@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_name": model_name,
        "feature_count": len(feature_cols),
        "threshold": float(threshold),
    }


@app.post("/predict")
def predict(req: PredictRequest):
    try:
        # 없는 컬럼은 0으로 채우고, 필요 없는 키는 자동 무시
        normalized_features = {
            col: req.features.get(col, 0) for col in feature_cols
        }

        result = predict_from_features(normalized_features)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

# curl -X POST "http://127.0.0.1:8000/predict" -H "Content-Type: application/json" -d @req.json

"""
result = predict_from_features(
    {
        # -------------------------
        # 시간 관련
        # -------------------------
        # 이 주소가 과거에 "보낸 거래"들 사이의 평균 시간 간격(분)
        # 서버가 이 주소의 송신 거래 시각들을 시간순으로 저장해두고, 인접 거래 시각 차이들의 평균으로 계산
        "Avg min between sent tnx": 0,
        # 이 주소가 과거에 "받은 거래"들 사이의 평균 시간 간격(분)
        # 서버가 이 주소의 수신 거래 시각들을 저장해두고 평균 간격 계산
        "Avg min between received tnx": 36572.61,
        # 이 주소의 첫 거래 시각과 마지막 거래 시각 차이(분) = (마지막 거래 시각 - 첫 거래 시각)
        "Time Diff between first and last (Mins)": 182863.07,

        # -------------------------
        # 기본 거래 횟수 / 주소 수
        # -------------------------
        # 이 주소가 과거에 보낸 총 거래 수. 서버가 송신 발생할 때마다 +1
        "Sent tnx": 0,
        # 이 주소가 과거에 받은 총 거래 수. 서버가 수신 발생할 때마다 +1
        "Received Tnx": 5,
        # 이 주소가 생성한 스마트컨트랙트 수
        # 컨트랙트 생성 기능을 추적한다면 그 횟수 누적, # 없으면 보통 0으로 둘 가능성이 큼
        "Number of Created Contracts": 0,
        # 이 주소가 돈을 받을 때 상대방으로 등장한 "서로 다른 송신 주소" 개수
        # 예: A, B, C 세 주소에게서 받았으면 3
        # 서버가 수신 상대 주소 집합(set)을 관리하면 됨
        "Unique Received From Addresses": 3,
        # 이 주소가 돈을 보낼 때 상대방으로 등장한 "서로 다른 수신 주소" 개수
        # 서버가 송신 상대 주소 집합(set)을 관리하면 됨
        "Unique Sent To Addresses": 0,

        # -------------------------
        # 수신 금액 통계
        # -------------------------
        # 받은 단일 거래 금액들 중 최소값
        "min value received": 0,
        # 받은 단일 거래 금액들 중 최대값
        "max value received": 0,
        # 받은 단일 거래 금액들의 평균
        # = 총 수신액 / 수신 거래 수
        "avg val received": 0,

        # -------------------------
        # 송신 금액 통계
        # -------------------------
        # 보낸 단일 거래 금액들 중 최소값
        "min val sent": 0,
        # 보낸 단일 거래 금액들 중 최대값
        "max val sent": 0,
        # 보낸 단일 거래 금액들의 평균
        # = 총 송신액 / 송신 거래 수
        "avg val sent": 0,

        # -------------------------
        # 컨트랙트 방향 송신 금액 통계
        # -------------------------
        # 컨트랙트 주소로 보낸 거래 금액 중 최소값, "상대 주소가 컨트랙트인지" 구분 가능한 경우에만 누적
        "min value sent to contract": 0,
        # 컨트랙트 주소로 보낸 거래 금액 중 최대값
        "max val sent to contract": 0,
        # 컨트랙트 주소로 보낸 거래 금액 평균
        "avg value sent to contract": 0,

        # -------------------------
        # 총합 집계
        # -------------------------
        # 전체 거래 수
        # 보낸 거래 + 받은 거래 + 필요하면 컨트랙트 생성 거래까지 포함
        "total transactions (including tnx to create contract": 6,
        # 이 주소가 보낸 총 ETH 양. 서버가 ETH 송신 발생할 때마다 누적
        "total Ether sent": 0,
        # 이 주소가 받은 총 ETH 양. 서버가 ETH 수신 발생할 때마다 누적
        "total ether received": 0,
        # 컨트랙트 주소로 보낸 총 ETH 양
        "total ether sent contracts": 0,
        # 현재 잔액성 지표. 가장 단순하게는 "총 수신 - 총 송신"으로 내부 계산 가능
        # 실제 체인과 맞추려면 web3/체인 조회값을 사용해도 됨
        "total ether balance": 0,

        # -------------------------
        # ERC20 관련 거래 횟수 / 총량
        # -------------------------
        # ERC20 토큰 관련 총 거래 수. 토큰 송수신 이벤트가 발생할 때마다 누적
        "Total ERC20 tnxs": 0,
        # 받은 ERC20 토큰 총량
        "ERC20 total Ether received": 0,
        # 보낸 ERC20 토큰 총량
        "ERC20 total ether sent": 0,
        # 컨트랙트 방향으로 보낸 ERC20 토큰 총량
        "ERC20 total Ether sent contract": 0,

        # -------------------------
        # ERC20 상대 주소 수
        # -------------------------
        # ERC20을 보낼 때 상대했던 서로 다른 주소 개수
        "ERC20 uniq sent addr": 0,
        # ERC20을 받을 때 상대했던 서로 다른 주소 개수
        "ERC20 uniq rec addr": 0,
        # ERC20 수신 관련 컨트랙트 주소 개수. 네 구현에서 추적 안 하면 0 또는 제외
        "ERC20 uniq rec contract addr": 0,

        # -------------------------
        # ERC20 시간 간격
        # -------------------------
        # ERC20 송신 거래들 사이 평균 시간 간격
        "ERC20 avg time between sent tnx": 0,
        # ERC20 수신 거래들 사이 평균 시간 간격
        "ERC20 avg time between rec tnx": 0,
        # ERC20 컨트랙트 관련 거래들 사이 평균 시간 간격
        "ERC20 avg time between contract tnx": 0,

        # -------------------------
        # ERC20 수신 금액 통계
        # -------------------------
        # 받은 ERC20 단일 거래 수량 최소값
        "ERC20 min val rec": 0,
        # 받은 ERC20 단일 거래 수량 최대값
        "ERC20 max val rec": 0,
        # 받은 ERC20 단일 거래 수량 평균
        "ERC20 avg val rec": 0,

        # -------------------------
        # ERC20 송신 금액 통계
        # -------------------------
        # 보낸 ERC20 단일 거래 수량 최소값
        "ERC20 min val sent": 0,
        # 보낸 ERC20 단일 거래 수량 최대값
        "ERC20 max val sent": 0,
        # 보낸 ERC20 단일 거래 수량 평균
        "ERC20 avg val sent": 0,

        # -------------------------
        # ERC20 컨트랙트 방향 송신 금액 통계
        # -------------------------
        # 컨트랙트 방향 ERC20 송신 최소값
        "ERC20 min val sent contract": 0,
        # 컨트랙트 방향 ERC20 송신 최대값
        "ERC20 max val sent contract": 0,
        # 컨트랙트 방향 ERC20 송신 평균값
        "ERC20 avg val sent contract": 0,

        # -------------------------
        # 파생 변수
        # -------------------------
        # ERC20 활동 유무 = Total ERC20 tnxs > 0 이면 1, 아니면 0
        "has_erc20_activity": 0,
        # 송신/수신 비율 = total Ether sent / (total ether received + 1e-9)
        # 수신보다 송신이 훨씬 많으면 큰 값
        "sent_received_ratio": 0,
        # 고유 상대방 비율 = Unique Sent To Addresses / (Sent tnx + 1e-9)
        # 송금 횟수 대비 얼마나 다양한 주소로 보냈는지
        "unique_counterparty_ratio": 0,
    }
)

print(result)
"""

