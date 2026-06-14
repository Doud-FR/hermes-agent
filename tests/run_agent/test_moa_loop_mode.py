import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.moa_loop import aggregate_moa_context
from run_agent import AIAgent


def _response(content="done", *, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None, model="fake-model")


def test_moa_mode_aggregates_reference_models_before_each_agent_iteration(monkeypatch):
    agent = AIAgent(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-opus-4.8",
        provider="openrouter",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=[],
        max_iterations=1,
    )
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _response("final")

    aggregate = MagicMock(return_value="reference synthesis")
    monkeypatch.setattr("agent.moa_loop.aggregate_moa_context", aggregate)

    result = agent.run_conversation(
        "solve this",
        moa_config={
            "reference_models": [
                {"provider": "openai-codex", "model": "gpt-5.5"},
                {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
            ],
            "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
        },
    )

    assert result["final_response"] == "final"
    aggregate.assert_called_once()
    kwargs = aggregate.call_args.kwargs
    assert kwargs["user_prompt"] == "solve this"
    assert [m["model"] for m in kwargs["reference_models"]] == [
        "gpt-5.5",
        "deepseek/deepseek-v4-pro",
    ]
    sent_messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
    sent_text = "\n".join(str(m.get("content", "")) for m in sent_messages)
    assert "reference synthesis" in sent_text


def test_moa_mode_runs_on_second_iteration_after_tool_result(monkeypatch):
    agent = AIAgent(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-opus-4.8",
        provider="openrouter",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=["file"],
        max_iterations=2,
    )
    agent.client = MagicMock()
    agent.client.chat.completions.create.side_effect = [
        _response(
            "",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    type="function",
                    function=SimpleNamespace(
                        name="search_files",
                        arguments=json.dumps({"pattern": "*.py", "target": "files", "path": "/tmp"}),
                    ),
                )
            ],
        ),
        _response("final"),
    ]

    aggregate_calls = []

    def aggregate(**kwargs):
        aggregate_calls.append(
            {
                "reference_models": kwargs["reference_models"],
                "aggregator": kwargs["aggregator"],
                "has_tool_result": any(m.get("role") == "tool" for m in kwargs["api_messages"]),
            }
        )
        return f"guidance {len(aggregate_calls)}"

    monkeypatch.setattr("agent.moa_loop.aggregate_moa_context", aggregate)

    result = agent.run_conversation(
        "find python files",
        moa_config={
            "reference_models": [
                {"provider": "openai-codex", "model": "gpt-5.5"},
                {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
            ],
            "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
        },
    )

    assert result["final_response"] == "final"
    assert len(aggregate_calls) == 2
    assert aggregate_calls[0]["has_tool_result"] is False
    assert aggregate_calls[1]["has_tool_result"] is True
    assert [slot["provider"] for slot in aggregate_calls[1]["reference_models"]] == [
        "openai-codex",
        "openrouter",
    ]
    assert aggregate_calls[1]["aggregator"] == {
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.8",
    }


def test_aggregate_moa_context_calls_mixed_providers(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=f"reply from {kwargs['provider']}:{kwargs['model']}", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    context = aggregate_moa_context(
        user_prompt="question",
        api_messages=[{"role": "user", "content": "question"}],
        reference_models=[
            {"provider": "openai-codex", "model": "gpt-5.5"},
            {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
        ],
        aggregator={"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
        max_tokens=200,
    )

    assert [(c["task"], c["provider"], c["model"]) for c in calls] == [
        ("moa_reference", "openai-codex", "gpt-5.5"),
        ("moa_reference", "openrouter", "deepseek/deepseek-v4-pro"),
        ("moa_aggregator", "openrouter", "anthropic/claude-opus-4.8"),
    ]
    assert "openai-codex:gpt-5.5" in context
    assert "openrouter:deepseek/deepseek-v4-pro" in context
