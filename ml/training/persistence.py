"""NGSAT ML model persistence — save/load with integrity verification.

Extracted from trainer.py in BE-15 refactoring.
Handles joblib serialization + SHA-256 sidecar integrity checks.
"""

from __future__ import annotations

import hashlib
import joblib
from pathlib import Path

from core.logger import logger

# ── Default model path ──
_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "trained"


def save_model(
    model_obj: dict,
    path: str | Path | None = None,
) -> Path:
    """Save model artifact to disk with integrity sidecar (.sha256).

    Args:
        model_obj: Dict with model, scaler, metadata.
        path: File path. Defaults to models/trained/price_rise_model.pkl.

    Returns:
        Path where model was saved.
    """
    save_path = Path(path) if path else _MODEL_DIR / "price_rise_model.pkl"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(model_obj, save_path)

    # Compute integrity hash and save as sidecar
    with open(save_path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()
    sha256_path = save_path.with_suffix(".pkl.sha256")
    sha256_path.write_text(file_hash + "\n")

    logger.info(f"ML 모델 저장: {save_path} (SHA-256: {file_hash[:16]}...)")
    return save_path


def load_model(path: str | Path | None = None) -> dict:
    """Load model artifact from disk with integrity verification.

    Args:
        path: File path. Defaults to models/trained/price_rise_model.pkl.

    Returns:
        Dict with model, scaler, metadata.

    Raises:
        RuntimeError: If integrity check fails.
    """
    load_path = Path(path) if path else _MODEL_DIR / "price_rise_model.pkl"

    # Verify integrity via sidecar (.sha256) file
    with open(load_path, "rb") as f:
        file_bytes = f.read()
    computed = hashlib.sha256(file_bytes).hexdigest()

    sha256_path = load_path.with_suffix(".pkl.sha256")
    if not sha256_path.exists():
        logger.warning(f"무결성 해시 파일 없음 — 검증 생략: {sha256_path}")
    else:
        stored = sha256_path.read_text().strip()
        if computed != stored:
            raise RuntimeError(
                f"모델 파일 무결성 검증 실패: {load_path}\n"
                f"  computed={computed}\n"
                f"  stored  ={stored}\n"
                f"  파일이 변조되었거나 손상되었습니다."
            )
        logger.info(f"모델 무결성 확인 완료 (SHA-256: {computed[:16]}...)")

    data = joblib.load(load_path)
    # Drop legacy in-file integrity hash if present
    data.pop("_integrity_hash", None)
    return data
