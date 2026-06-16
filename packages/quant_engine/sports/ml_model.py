"""
sports/ml_model.py — 足球 XGBoost 预测模型
=============================================
从 tracker.py 积累的已结算预测记录中学习，
与 Dixon-Coles 规则模型进行 A/B Brier Score 对比。

触发逻辑:
  - 首次训练: ≥20 条已结算记录（Elo + 实际结果）
  - 自动重训: 每新增 10 条记录触发一次
  - 预测时:   优先使用 ML 模型，数据不足则 fallback → DC

特征向量 (9维):
  elo_diff, elo_home, elo_away,
  lambda_home, lambda_away, lambda_ratio,
  league_avg, elo_diff_abs_scaled, is_high_gap

标签: 0=away, 1=draw, 2=home（XGBoost 多分类）

持久化:
  ~/.arthera/football_ml_model.pkl
  ~/.arthera/football_ml_report.json
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH  = Path.home() / ".arthera" / "football_ml_model.pkl"
_REPORT_PATH = Path.home() / ".arthera" / "football_ml_report.json"
_MIN_TRAIN   = 20
_RETRAIN_EVERY = 10

try:
    from xgboost import XGBClassifier
    _HAS_XGB = True
except ImportError:
    try:
        import lightgbm as lgb
        _HAS_XGB = False
        _HAS_LGB = True
    except ImportError:
        _HAS_XGB = False
        _HAS_LGB = False

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score, StratifiedKFold
    _HAS_SK = True
except ImportError:
    _HAS_SK = False


# ── 特征提取 ──────────────────────────────────────────────────────────────────

_FEATURE_NAMES = [
    "elo_diff", "elo_home", "elo_away",
    "lambda_home", "lambda_away", "lambda_ratio",
    "league_avg", "elo_gap_scaled", "is_high_gap",
]


def _extract_features(record: Dict) -> Optional[np.ndarray]:
    """从一条预测记录提取特征向量，缺字段返回 None。"""
    elo_h = record.get("home_elo")
    elo_a = record.get("away_elo")
    lh    = record.get("lambda_home")
    la    = record.get("lambda_away")
    avg   = record.get("league_avg", 1.35)

    if any(v is None for v in [elo_h, elo_a, lh, la]):
        return None

    elo_h, elo_a, lh, la, avg = float(elo_h), float(elo_a), float(lh), float(la), float(avg)
    diff = elo_h - elo_a

    return np.array([
        diff,                           # Elo 差
        elo_h,                          # 主队 Elo
        elo_a,                          # 客队 Elo
        lh,                             # 主队期望进球
        la,                             # 客队期望进球
        lh / (la + 1e-6),              # λ 比值（反映实力差距）
        avg,                            # 赛事场均进球
        abs(diff) / 400.0,             # 标准化 Elo 差（400=1个标准差）
        1.0 if abs(diff) > 200 else 0.0,  # 悬殊场次标志
    ], dtype=np.float32)


def _result_to_label(result: str) -> int:
    """home=2, draw=1, away=0"""
    return {"home": 2, "draw": 1, "away": 0}.get(result, -1)


# ── 训练器 ────────────────────────────────────────────────────────────────────

class FootballMLModel:
    """
    足球 XGBoost/LightGBM 预测器。

    用法:
        m = FootballMLModel.load_or_train()
        if m.is_ready:
            p = m.predict(record)   # {"home_win": 0.72, "draw": 0.18, "away_win": 0.10}
    """

    def __init__(self):
        self._model   = None
        self._scaler  = None
        self._report: Dict = {}
        self._n_trained = 0

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    # ── 训练 ──────────────────────────────────────────────────────────────────

    def train(self, records: Optional[List[Dict]] = None) -> Dict:
        """
        从 tracker 记录中训练。records 为 None 时自动从磁盘加载。
        返回训练报告 dict。
        """
        if not (_HAS_XGB or _HAS_LGB):
            return {"error": "pip install xgboost 或 lightgbm 后重试"}
        if not _HAS_SK:
            return {"error": "pip install scikit-learn 后重试"}

        if records is None:
            records = _load_settled_records()

        # 过滤出含完整特征的记录
        X_rows, y_rows = [], []
        for r in records:
            label = _result_to_label(r.get("result", ""))
            if label == -1:
                continue
            feat = _extract_features(r)
            if feat is None:
                continue
            X_rows.append(feat)
            y_rows.append(label)

        n = len(X_rows)
        if n < _MIN_TRAIN:
            return {"status": "waiting", "n": n, "need": _MIN_TRAIN,
                    "message": f"需要 {_MIN_TRAIN} 条完整记录，当前 {n} 条"}

        X = np.array(X_rows)
        y = np.array(y_rows)

        # 标准化
        scaler = StandardScaler()
        X_s = scaler.fit_transform(X)

        # 模型
        if _HAS_XGB:
            model = XGBClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                reg_alpha=0.1, reg_lambda=0.5,
                objective="multi:softprob", num_class=3,
                eval_metric="mlogloss", use_label_encoder=False,
                random_state=42, verbosity=0,
            )
        else:
            import lightgbm as lgb
            model = lgb.LGBMClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                num_class=3, objective="multiclass",
                feature_fraction=0.8, bagging_fraction=0.8,
                reg_alpha=0.1, reg_lambda=0.5,
                verbose=-1, random_state=42,
            )

        # 走步交叉验证（时序感知：按时间顺序分折）
        cv_briers = _walk_forward_cv(model, X_s, y, n_splits=min(5, n // 4))

        # 全量重训练
        model.fit(X_s, y)

        self._model   = model
        self._scaler  = scaler
        self._n_trained = n

        # CV Brier vs DC Brier（走步验证，公平对比）
        dc_brier   = _dc_brier_from_records(records[:n])
        cv_mean    = float(np.mean(cv_briers)) if cv_briers else None
        # improvement = DC - CV_ML（正值表示 ML 更准，使用 CV 避免训练集过拟合）
        improvement = round(dc_brier - cv_mean, 4) if cv_mean is not None else None

        lib = "XGBoost" if _HAS_XGB else "LightGBM"
        self._report = {
            "lib":           lib,
            "n_samples":     int(n),
            "cv_brier_mean": round(cv_mean, 4) if cv_mean is not None else None,
            "cv_brier_std":  round(float(np.std(cv_briers)), 4) if cv_briers else None,
            "dc_brier":      round(float(dc_brier), 4),
            "improvement":   improvement,  # >0 = ML 更准（基于 CV，可信）
            "trained_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "feature_names": _FEATURE_NAMES,
        }

        _save_model(self._model, self._scaler, self._report)
        logger.info(
            f"[FootballML] {lib} 训练完成 n={n}  "
            f"CV Brier={self._report.get('cv_brier_mean')}  "
            f"DC Brier={dc_brier:.4f}  提升={self._report['improvement']:+.4f}"
        )
        return self._report

    # ── 预测 ──────────────────────────────────────────────────────────────────

    def predict(self, record: Dict) -> Optional[Dict[str, float]]:
        """
        从预测记录（含 elo/lambda）输出 ML 概率。
        返回 None 表示特征不完整，调用方应 fallback 到 DC。
        """
        if not self.is_ready:
            return None
        feat = _extract_features(record)
        if feat is None:
            return None

        feat_s = self._scaler.transform(feat.reshape(1, -1))
        proba  = self._model.predict_proba(feat_s)[0]  # [away, draw, home]
        return {
            "away_win": round(float(proba[0]), 4),
            "draw":     round(float(proba[1]), 4),
            "home_win": round(float(proba[2]), 4),
            "model":    "XGB+Elo+λ",
        }

    @property
    def report(self) -> Dict:
        return self._report

    # ── 加载/保存 ─────────────────────────────────────────────────────────────

    @classmethod
    def load_or_train(cls, force_train: bool = False) -> "FootballMLModel":
        """加载已存模型，若不存在或需重训则自动训练。"""
        m = cls()
        if _MODEL_PATH.exists() and not force_train:
            try:
                payload = pickle.loads(_MODEL_PATH.read_bytes())
                m._model   = payload["model"]
                m._scaler  = payload["scaler"]
                m._report  = payload.get("report", {})
                m._n_trained = payload.get("n_trained", 0)

                # 检查是否需要重训
                records = _load_settled_records()
                if len(records) >= m._n_trained + _RETRAIN_EVERY:
                    logger.info("[FootballML] 新增 ≥10 条记录，触发重训")
                    m.train(records)
                return m
            except Exception as e:
                logger.warning(f"[FootballML] 加载失败: {e}，重新训练")

        m.train()
        return m


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_settled_records() -> List[Dict]:
    """从 tracker 加载已结算预测记录。"""
    try:
        from .tracker import _PRED_PATH, _load_json
        records = _load_json(_PRED_PATH, [])
        return [r for r in records if r.get("result") and r.get("brier_score") is not None]
    except Exception:
        return []


def _walk_forward_cv(model, X: np.ndarray, y: np.ndarray, n_splits: int = 5) -> List[float]:
    """时序感知交叉验证，返回每折 Brier Score。"""
    import copy
    n = len(X)
    fold_size = max(4, n // (n_splits + 1))
    briers = []
    for i in range(n_splits):
        tr_end = (i + 1) * fold_size
        te_end = tr_end + fold_size
        if te_end > n:
            break
        y_tr = y[:tr_end]
        # 跳过训练集类别不足的折（XGBoost 要求所有类别都出现）
        if len(np.unique(y_tr)) < 3:
            continue
        try:
            m_copy = copy.deepcopy(model)
            m_copy.fit(X[:tr_end], y_tr)
            proba = m_copy.predict_proba(X[tr_end:te_end])
            if proba.shape[1] == 3:
                briers.append(_brier_mc(proba, y[tr_end:te_end]))
        except Exception:
            continue
    return briers


def _brier_mc(proba: np.ndarray, y: np.ndarray) -> float:
    """多分类 Brier Score。"""
    total = 0.0
    n_classes = proba.shape[1]
    for i, yi in enumerate(y):
        for c in range(n_classes):
            total += (proba[i, c] - (1.0 if yi == c else 0.0)) ** 2
    return total / max(len(y), 1)


def _dc_brier_from_records(records: List[Dict]) -> float:
    """用记录里已存的 brier_score（DC 模型）计算均值。"""
    scores = [r["brier_score"] for r in records if r.get("brier_score") is not None]
    return float(np.mean(scores)) if scores else 0.5


def _save_model(model, scaler, report: Dict) -> None:
    try:
        n = report.get("n_samples", 0)
        payload = {"model": model, "scaler": scaler, "report": report, "n_trained": n}
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_PATH.write_bytes(pickle.dumps(payload))
        # JSON 序列化：将 numpy 类型转换为 Python 原生类型
        def _to_native(obj):
            if isinstance(obj, (np.floating, np.float32, np.float64)): return float(obj)
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            return obj
        safe_report = json.loads(json.dumps(report, default=_to_native))
        _REPORT_PATH.write_text(json.dumps(safe_report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[FootballML] 保存失败: {e}")


# ── 单例 ─────────────────────────────────────────────────────────────────────

_instance: Optional[FootballMLModel] = None


def get_football_ml() -> FootballMLModel:
    global _instance
    if _instance is None:
        _instance = FootballMLModel.load_or_train()
    return _instance
