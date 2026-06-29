"""Quick retrain test to diagnose AUC=0.655 issue."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.config import load_config
from data.real_data_provider import RealDataProvider
from ml.training.trainer import PriceRiseModel, TrainingResult
from ml.features.builder import build_training_dataset, FEATURE_NAMES


async def test():
    cfg = load_config()
    provider = RealDataProvider(training_days=60)

    print("Loading data...")
    universe, _ = await provider.load()
    print(f"Loaded {len(universe)} stocks")

    codes = [info.code for info, _ in universe]
    prices_list = [prices for _, prices in universe]

    min_len = min(len(p) for p in prices_list)
    max_len = max(len(p) for p in prices_list)
    print(f"Stocks: {len(codes)}, Price bars: min={min_len}, max={max_len}")

    # Build training dataset
    X, y, fnames = build_training_dataset(prices_list, codes, forward_days=5, forward_threshold=0.02)
    print(f"Dataset shape: X={X.shape}, y={y.shape}")
    if len(y) > 0:
        print(f"y distribution: sum={y.sum()}, total={len(y)}, ratio={y.sum()/len(y):.3f}")

    if len(X) < 50:
        print(f"INSUFFICIENT DATA: {len(X)} < 50")
        return

    # 1) Test _single_model_retrain
    print("\n--- _single_model_retrain ---")
    model = PriceRiseModel("gradient_boosting", auto_select_model=True)
    model._last_auc = 0.6  # Simulate previous best
    changed, result = model._single_model_retrain(X, y)
    print(f"changed={changed}, auc={result.auc:.4f}, last_auc={model._last_auc:.4f}")

    # 2) Test _multi_model_retrain
    print("\n--- _multi_model_retrain ---")
    model2 = PriceRiseModel("gradient_boosting", auto_select_model=True)
    model2._last_auc = 0.6
    changed2, result2 = model2._multi_model_retrain(X, y)
    print(f"changed={changed2}, auc={result2.auc:.4f}, last_auc={model2._last_auc:.4f}, type={model2.model_type}")

    # 3) Test auto_retrain directly
    print("\n--- auto_retrain ---")
    model3 = PriceRiseModel("logistic", auto_select_model=True)
    model3._last_auc = 0.0
    changed3, result3 = model3.auto_retrain(prices_list, codes)
    print(f"changed={changed3}, auc={result3.auc:.4f}, last_auc={model3._last_auc:.4f}, type={model3.model_type}")

    await provider.close()


if __name__ == "__main__":
    asyncio.run(test())
