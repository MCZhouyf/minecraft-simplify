from experiments.run_secondary_retry import _pick_model


def test_pick_model_cycles():
    models = ["gpt-5.2", "gpt-5.1-2025-11-13", "gpt-5.1-chat"]
    assert _pick_model(models, 1) == "gpt-5.2"
    assert _pick_model(models, 2) == "gpt-5.1-2025-11-13"
    assert _pick_model(models, 3) == "gpt-5.1-chat"
    assert _pick_model(models, 4) == "gpt-5.2"


def test_pick_model_empty():
    assert _pick_model([], 1) is None
