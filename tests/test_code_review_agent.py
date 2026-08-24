from aria_code.agents.base import BaseAgent
from aria_code.agents.code_review import CodeReviewAgent
from aria_code.packages.adk_bridge import CodeReviewTools


def test_time_sensitive_agent_policy_has_no_hardcoded_market_claims():
    policy = BaseAgent._TIME_SENSITIVE_FACT_POLICY

    assert "SPCX" not in policy
    assert "World Cup" not in policy
    assert "unverified" in policy


def test_code_review_agent_reports_actionable_security_and_correctness_findings():
    source = '''
token = "ghp_abcdefghijklmnopqrstuvwxyz"
subprocess.run(user_command, shell=True)
try:
    do_work()
except:
    pass
'''

    findings = CodeReviewAgent.review_source(source, filename="service.py")
    rules = {finding.rule for finding in findings}

    assert {"hardcoded-secret", "shell-true", "bare-except"} <= rules
    assert all(finding.evidence != "ghp_abcdefghijklmnopqrstuvwxyz" for finding in findings)


def test_code_review_agent_returns_syntax_error_for_invalid_python():
    findings = CodeReviewAgent.review_source("def broken(:\n", filename="broken.py")

    assert findings[0].severity == "critical"
    assert findings[0].rule == "python-syntax-error"


def test_adk_code_review_tool_is_text_only_and_sanitizes_filename():
    result = CodeReviewTools().review_code(
        "value = eval(user_input)\n",
        filename="/private/project/unsafe.py",
    )

    assert result["success"] is True
    assert result["filename"] == "unsafe.py"
    assert result["counts"]["high"] == 1
    assert result["findings"][0]["rule"] == "dynamic-execution"
    assert "read a file" in result["limitations"][0]


def test_adk_code_review_tool_rejects_oversized_source():
    result = CodeReviewTools().review_code("x" * (CodeReviewAgent.MAX_SOURCE_CHARS + 1))

    assert result["success"] is False
    assert "split" in result["error"]
