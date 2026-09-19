"""Treina e avalia o motor preditivo local do CAIS.

O artefato salvo é um Pipeline completo: ele recebe as colunas brutas e aplica
OneHotEncoder(handle_unknown="ignore") antes do RandomForest. Isso elimina a
divergência entre treino e inferência da implementação inicial.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "dataset_seops.csv"
MODEL_PATH = ROOT / "motor_preditivo_seops.pkl"
FEATURES_PATH = ROOT / "features_modelo.pkl"
METRICS_PATH = ROOT / "modelo_metricas.json"
TARGET = "acao_preventiva_necessaria"
CATEGORICAL = ["local", "iluminacao_fonte"]
RANDOM_STATE = 42


def build_pipeline(numeric_columns: list[str]) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
            ("numeric", "passthrough", numeric_columns),
        ]
    )
    classifier = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    return Pipeline([("preprocess", preprocessor), ("classifier", classifier)])


def main() -> None:
    print("0. Carregando dataset...")
    dataframe = pd.read_csv(DATASET_PATH)
    input_columns = [column for column in dataframe.columns if column != TARGET]
    numeric_columns = [column for column in input_columns if column not in CATEGORICAL]
    X = dataframe[input_columns]
    y = dataframe[TARGET].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.20,
        random_state=RANDOM_STATE,
        stratify=y,
    )
    pipeline = build_pipeline(numeric_columns)
    print("1. Treinando Pipeline com balanceamento de classes...")
    pipeline.fit(X_train, y_train)

    probabilities = pipeline.predict_proba(X_test)[:, 1]
    predictions = (probabilities >= 0.50).astype(int)
    report = classification_report(y_test, predictions, output_dict=True, zero_division=0)
    matrix = confusion_matrix(y_test, predictions)
    metrics = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "rows": int(len(dataframe)),
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "positive_rate": round(float(y.mean()), 4),
            "nature": "hibrida",
            "warning": (
                "A base combina dados públicos e variáveis sintéticas. Estas métricas validam "
                "o protótipo e não representam desempenho comprovado em operação real."
            ),
        },
        "split": {"test_size": 0.20, "stratified": True, "random_state": RANDOM_STATE},
        "threshold": 0.50,
        "metrics": {
            "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
            "precision_positive": round(float(precision_score(y_test, predictions, zero_division=0)), 4),
            "recall_positive": round(float(recall_score(y_test, predictions, zero_division=0)), 4),
            "f1_positive": round(float(f1_score(y_test, predictions, zero_division=0)), 4),
            "roc_auc": round(float(roc_auc_score(y_test, probabilities)), 4),
            "pr_auc": round(float(average_precision_score(y_test, probabilities)), 4),
            "confusion_matrix": {
                "true_negative": int(matrix[0, 0]),
                "false_positive": int(matrix[0, 1]),
                "false_negative": int(matrix[1, 0]),
                "true_positive": int(matrix[1, 1]),
            },
            "classification_report": report,
        },
        "model": {
            "algorithm": "RandomForestClassifier",
            "n_estimators": 300,
            "max_depth": 12,
            "min_samples_leaf": 2,
            "class_weight": "balanced_subsample",
            "sklearn_version": sklearn.__version__,
        },
    }

    feature_names = pipeline.named_steps["preprocess"].get_feature_names_out()
    importances = pipeline.named_steps["classifier"].feature_importances_
    top_features = sorted(
        zip(feature_names, importances, strict=True), key=lambda item: item[1], reverse=True
    )[:15]
    metrics["model"]["top_features"] = [
        {"feature": str(name), "importance": round(float(importance), 5)}
        for name, importance in top_features
    ]

    print("2. Salvando pipeline, contrato de entrada e métricas...")
    joblib.dump(pipeline, MODEL_PATH)
    joblib.dump(input_columns, FEATURES_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = metrics["metrics"]
    print(f"Acurácia: {summary['accuracy']:.3f}")
    print(f"Precisão positiva: {summary['precision_positive']:.3f}")
    print(f"Recall positivo: {summary['recall_positive']:.3f}")
    print(f"F1 positivo: {summary['f1_positive']:.3f}")
    print(f"ROC-AUC: {summary['roc_auc']:.3f} | PR-AUC: {summary['pr_auc']:.3f}")
    print(f"Matriz de confusão: {summary['confusion_matrix']}")
    print("✅ Artefatos atualizados.")


if __name__ == "__main__":
    main()
