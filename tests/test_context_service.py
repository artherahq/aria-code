from aria_code.packages.aria_services.context import ContextPolicy, ContextService, build_context_service


def test_context_service_decides_from_incoming_pressure():
    service = build_context_service(max_tokens=9000, threshold=0.78, min_messages=8)
    messages = [{"role": "user", "content": "x" * 3000} for _ in range(8)]

    decision = service.compaction_decision(messages, extra_content="y" * 1200)

    assert decision.should_compact is True
    assert decision.reason == "threshold_exceeded"
    assert decision.estimated_tokens == 8400
    assert decision.fill_pct == 93
    assert decision.target_tokens == 4950


def test_context_service_stays_quiet_for_small_sessions():
    service = build_context_service(max_tokens=2000, threshold=0.50, min_messages=8)
    messages = [{"role": "user", "content": "x" * 500} for _ in range(3)]

    decision = service.compaction_decision(messages)

    assert decision.should_compact is False
    assert decision.reason == "message_count_below_minimum"
    assert decision.message_count == 3


def test_context_policy_normalizes_bad_config_values():
    service = ContextService(ContextPolicy(max_tokens="bad", threshold="bad", min_messages="bad"))

    assert service.policy.max_tokens == 16384
    assert service.policy.threshold == 0.78
    assert service.policy.min_messages == 8


def test_context_service_local_compaction_preserves_errors_and_tail():
    service = ContextService(ContextPolicy(max_tokens=4096, tail_messages=4))
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "old assistant " + ("a" * 900)},
        {
            "role": "tool",
            "content": "\n".join([
                "run step 1",
                "Traceback: boom",
                "Exception: failed request",
                "extra payload " + ("b" * 900),
            ]),
        },
    ]
    messages.extend({"role": "user", "content": f"tail {idx}"} for idx in range(4))

    compacted = service.compact_messages(messages, max_chars=100)

    assert compacted[0]["role"] == "system"
    joined = "\n".join(str(message.get("content", "")) for message in compacted)
    assert len(compacted) <= len(messages)
    assert "Traceback: boom" in joined
    assert "failed request" in joined
    assert compacted[-1]["content"] == "tail 3"


def test_context_service_summary_prompt_and_envelope_shape():
    service = ContextService(ContextPolicy(summary_tail_messages=2))
    messages = [
        {"role": "user", "content": "Analyze AAPL"},
        {"role": "assistant", "content": "AAPL price is 298."},
        {"role": "tool", "content": "quote ok"},
        {"role": "user", "content": "Continue"},
    ]

    prompt = service.build_summary_prompt(messages)
    envelope = service.build_summary_envelope(messages, "Session summary: AAPL was analyzed.")

    assert "AAPL" in prompt
    assert "DENSE SUMMARY" in prompt
    assert envelope.old_message_count == 4
    assert envelope.tail_message_count == 2
    assert envelope.messages[0]["role"] == "user"
    assert "Session summary: AAPL was analyzed." in envelope.messages[0]["content"]
    assert envelope.messages[-1]["content"] == "Continue"


def test_summary_envelope_bounds_recent_output_and_drops_runtime_markers():
    service = ContextService(ContextPolicy(
        max_tokens=4096,
        target_ratio=0.50,
        summary_tail_messages=4,
    ))
    huge = "report body\n" + ("x" * 30_000) + "\n*[model stopped — repetition detected]*"
    messages = [
        {"role": "user", "content": "Analyze 000518"},
        {"role": "assistant", "content": huge},
        {"role": "user", "content": "继续完善"},
    ]

    envelope = service.build_summary_envelope(messages, "Session summary: report generated.")
    total_chars = sum(len(str(message.get("content", ""))) for message in envelope.messages)
    joined = "\n".join(str(message.get("content", "")) for message in envelope.messages)

    assert total_chars < 4096 * 3 * 0.55
    assert "model stopped" not in joined
    assert "recent message compacted" in joined


def test_summary_envelope_does_not_nest_previous_summary_wrapper():
    service = ContextService(ContextPolicy(summary_tail_messages=4))
    messages = [
        {"role": "user", "content": "[会话摘要 — 早期对话已压缩]\nold"},
        {"role": "assistant", "content": "已获取摘要，继续之前的工作。"},
        {"role": "user", "content": "current task"},
    ]

    envelope = service.build_summary_envelope(messages, "Session summary: current.")
    joined = "\n".join(str(message.get("content", "")) for message in envelope.messages)

    assert "old" not in joined
    assert joined.count("Session summary: current.") == 1


def test_message_processing_uses_context_service_compatibility_layer():
    from apps.cli.message_processing import context_compaction_decision, estimate_message_tokens

    messages = [{"role": "user", "content": "x" * 3000} for _ in range(8)]

    assert estimate_message_tokens(messages) == 8000
    decision = context_compaction_decision(
        messages,
        model_key="qwen2.5-coder:1.5b",
        extra_content="y" * 1200,
        threshold=0.78,
    )

    assert decision["should_compact"] is True
    assert decision["reason"] == "threshold_exceeded"
