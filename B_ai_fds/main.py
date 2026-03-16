import joblib
import os

# 어디서 실행하든 main.py 기준으로 pkl 파일을 찾도록 절대 경로 사용
_dir = os.path.dirname(os.path.abspath(__file__))

# 실제 이더리움 데이터(Kaggle) 학습 모델 사용
# → 자체 시뮬레이션 데이터로 학습 시 순환 검증 문제 발생, 실 데이터 모델이 학술적으로 유효
real_model_path = os.path.join(_dir, "fraud_model_artifact.pkl")

artifact = joblib.load(real_model_path)
print(f"[+] 실 이더리움 데이터 모델 로드: {artifact['model_name']}")

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
