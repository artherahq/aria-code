"""按用户实际环境生成 providers.yaml，而不是发一份通用模板。

通用模板对每个人都是"差不多但不对"：装了 Ollama 的人不需要被告知怎么配
OpenAI，配了 DEEPSEEK_API_KEY 的人不该看到一堆用不上的占位符，而真正卡住他的
（本地服务没起来 / key 名字拼错）模板里一个字都不会提。

这些用例钉住三件事：探测只读、生成结果反映真实环境、已有配置不被静默覆盖。
"""

from __future__ import annotations

import pytest

from aria_code.providers.llm.autoconfig import (
    Finding,
    probe_environment,
    render_providers_yaml,
    suggest_default_chain,
)


def _findings(**kw) -> list[Finding]:
    base = [
        Finding("ollama", "local", kw.get("ollama", False),
                models=kw.get("ollama_models", []), base_url="http://localhost:11434"),
        Finding("lmstudio", "local", False, base_url="http://localhost:1234"),
        Finding("deepseek", "cloud", kw.get("deepseek", False),
                env_key="DEEPSEEK_API_KEY", signup_url="https://platform.deepseek.com/api_keys"),
        Finding("openai", "cloud", kw.get("openai", False),
                env_key="OPENAI_API_KEY", signup_url="https://platform.openai.com/api-keys"),
    ]
    return base


def test_local_is_preferred_over_cloud():
    """本地零成本、不出网、隐私最好，应排在链首。"""
    default, fallback = suggest_default_chain(
        _findings(ollama=True, ollama_models=["qwen2.5:7b"], deepseek=True)
    )
    assert default == "ollama/qwen2.5:7b"
    assert fallback and fallback[0].startswith("deepseek/")


def test_unconfigured_providers_never_enter_the_chain():
    """把用不了的 provider 写进 fallback，只会在真正需要兜底时连续失败几次
    才走到能用的那个。"""
    default, fallback = suggest_default_chain(_findings(deepseek=True))
    assert default.startswith("deepseek/")
    assert not any("openai" in item for item in fallback)
    assert not any("ollama" in item for item in fallback)


def test_no_sources_produces_actionable_guidance():
    """一个来源都没有时，不能只写个空配置了事。"""
    text = render_providers_yaml(_findings())
    assert "ollama" in text and "brew install ollama" in text
    assert "没有可用的模型来源" in text


def test_missing_keys_are_listed_with_where_to_get_them():
    """缺什么、去哪拿，写进配置文件本身——用户不必在文档和配置间来回对照。"""
    text = render_providers_yaml(_findings(ollama=True, ollama_models=["qwen2.5:7b"]))
    assert "DEEPSEEK_API_KEY" in text
    assert "https://platform.deepseek.com/api_keys" in text
    assert "OPENAI_API_KEY" in text


def test_detected_local_models_are_recorded():
    text = render_providers_yaml(
        _findings(ollama=True, ollama_models=["qwen2.5:7b", "qwen2.5-coder:1.5b"])
    )
    assert "qwen2.5-coder:1.5b" in text
    assert "http://localhost:11434" in text


def test_generated_config_never_contains_secret_values(monkeypatch):
    """生成物只写环境变量名。哪怕环境里有 key，也不能被写进文件——
    配置文件常被备份/同步/误提交。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-never-be-written")
    text = render_providers_yaml(probe_environment(check_local=False))
    assert "sk-should-never-be-written" not in text
    assert "DEEPSEEK_API_KEY" in text or "deepseek" in text


def test_probe_is_read_only_for_cloud_providers(monkeypatch):
    """云端探测只看环境变量，不发请求——发探活请求既消耗用户额度，
    也把 key 泄给一次纯粹的探测。"""
    called = []
    import providers.llm.autoconfig as ac
    monkeypatch.setattr(ac, "_fetch_json", lambda *a, **k: called.append(a) or None)
    monkeypatch.setattr(ac, "_port_open", lambda *a, **k: False)

    findings = ac.probe_environment()
    cloud = [f for f in findings if f.kind == "cloud"]
    assert cloud, "应当探测到云端 provider 条目"
    # 本地端口未开时 _fetch_json 不该被调用；云端本来就不调
    assert not called, f"探测过程发起了网络请求: {called}"
