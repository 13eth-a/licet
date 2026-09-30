import pytest

from licet.config import load_config


def test_load_config_from_mapping():
    config = load_config(
        {
            "LICET_AGENT_MODEL": "primary-test",
            "LICET_FALLBACK_MODEL": "fallback-test",
            "LICET_MAX_STEPS": "7",
            "SOLARI_BASE_URL": "https://solari.test",
            "OPENAI_API_KEY": "sk-test",
        }
    )
    assert config.agent_model == "primary-test"
    assert config.fallback_model == "fallback-test"
    assert config.max_steps == 7
    assert config.solari_base_url == "https://solari.test"
    assert config.openai_api_key == "sk-test"


def test_model_defaults_are_openai_ids_that_exist():
    """the provider swap: slugs are validated config now, not runtime surprises"""
    config = load_config({})

    assert config.agent_model == "gpt-5.6-sol"
    assert config.fallback_model == "gpt-5.4-mini"
    assert not hasattr(config, "anthropic_api_key")


def test_model_limits_are_validated():
    assert load_config({"LICET_MODEL_TIMEOUT": "30", "LICET_MODEL_RETRIES": "0"}).model_max_retries == 0
    with pytest.raises(ValueError, match="LICET_MODEL_TIMEOUT"):
        load_config({"LICET_MODEL_TIMEOUT": "soon"})
    with pytest.raises(ValueError, match="greater than 0"):
        load_config({"LICET_MODEL_TIMEOUT": "0"})
    with pytest.raises(ValueError, match="LICET_MODEL_RETRIES"):
        load_config({"LICET_MODEL_RETRIES": "many"})
    with pytest.raises(ValueError, match="cannot be negative"):
        load_config({"LICET_MODEL_RETRIES": "-1"})


def test_max_steps_must_be_positive_integer():
    with pytest.raises(ValueError, match="LICET_MAX_STEPS"):
        load_config({"LICET_MAX_STEPS": "zero"})
    with pytest.raises(ValueError, match="at least 1"):
        load_config({"LICET_MAX_STEPS": "0"})
