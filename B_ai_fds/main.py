import joblib
import os

# 어디서 실행하든 main.py 기준으로 pkl 파일을 찾도록 절대 경로 사용
_dir = os.path.dirname(os.path.abspath(__file__))

# Ganache 맞춤 모델 우선 로드, 없으면 기존 모델 사용
ganache_model_path = os.path.join(_dir, "ganache_model_artifact.pkl")
legacy_model_path = os.path.join(_dir, "fraud_model_artifact.pkl")

if os.path.exists(ganache_model_path):
    artifact = joblib.load(ganache_model_path)
    print(f"[+] Ganache 맞춤 모델 로드: {artifact['model_name']}")
else:
    artifact = joblib.load(legacy_model_path)
    print(f"[!] 기존 모델 사용: {artifact['model_name']} (train_ganache_model.py를 실행하세요)")

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
