import asyncio
from harbor.agent import AgentOrchestrator
from harbor.llm import ModelClient, ModelConfig


def run(text, handlers, **kwargs):
    return asyncio.run(AgentOrchestrator(ModelClient()).run(text, {}, handlers, "c", **kwargs))


def test_models_classify_utterance_and_ground_tool_result():
    client = ModelClient(ModelConfig(intent_model="classifier", dialog_model="planner"))
    result = asyncio.run(
        AgentOrchestrator(client).run("book a visit", {}, {"search_availability": lambda: ["engine-slot"]}, "c")
    )
    assert result["status"] == "completed"
    assert result["results"][0]["result"] == ["engine-slot"]
    assert [x["model"] for x in client.usage] == ["classifier", "planner", "planner"]


def test_unverified_write_and_no_confirmation_do_not_execute():
    def fail():
        raise AssertionError("must not execute")

    assert run("cancel appointment", {"cancel": fail})["status"] == "confirmation_required"
    assert run("cancel appointment", {"cancel": fail}, verified=True)["status"] == "confirmation_required"


def test_explicit_verified_write_executes_once():
    calls = []
    result = run("cancel appointment", {"cancel": lambda: calls.append(1)}, verified=True, user_confirmed=True)
    assert result["status"] == "completed" and calls == [1]


def test_safety_before_any_model_or_tool():
    client = ModelClient()
    result = asyncio.run(AgentOrchestrator(client).run("chest pain", {}, {}, "c"))
    assert result["rule"] == "H02" and not client.usage


def test_unavailable_tool_and_exceptions_fail_closed():
    assert run("book visit", {})["status"] == "handoff"

    def fail():
        raise ValueError("sensitive values")

    result = run("book visit", {"search_availability": fail})
    assert result["status"] == "handoff" and "sensitive" not in str(result)


def test_explicit_book_requires_confirmation_and_never_auto_books():
    calls = []
    result = run(
        "book visit", {"book": lambda: calls.append(1)}, requested_action="book", verified=True, user_confirmed=True
    )
    assert result["status"] == "completed" and calls == [1]


def test_budget_stops_without_tool_execution():
    client = ModelClient(ModelConfig(max_steps=1))
    calls = []
    result = asyncio.run(
        AgentOrchestrator(client).run("book visit", {}, {"search_availability": lambda: calls.append(1)}, "c")
    )
    assert result["status"] == "handoff" and not calls


def test_real_adapter_receives_redacted_text_not_existing_intent():
    import json

    payloads = []

    async def transport(payload):
        payloads.append(payload)
        context = json.loads(payload["messages"][1]["content"])
        result = {"intent": "book", "handoff": False}
        if context["purpose"] == "dialog":
            result["next_action"] = context["allowed_actions"][0]
        return {
            "choices": [{"message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    client = ModelClient(ModelConfig(provider="test", max_conversation_tokens=10000), transport)
    result = asyncio.run(
        AgentOrchestrator(client).run(
            "Please book for Alice Synthetic, alice@example.com, 1990-01-01",
            {},
            {"search_availability": lambda: ["slot-engine"]},
            "c",
            known_personal_values=("Alice Synthetic",),
        )
    )
    assert result["status"] == "completed"
    actual = json.loads(payloads[0]["messages"][1]["content"])
    assert "utterance" in actual and "intent" not in actual
    assert "Alice" not in str(payloads) and "alice@example" not in str(payloads) and "1990-01-01" not in str(payloads)
    assert "slot-engine" not in str(payloads)


def test_prompt_explicitly_defines_standalone_reply_codes():
    from harbor.llm import MODEL_PROMPT, classify_intent

    assert "C means confirm, R means reschedule, X means cancel" in MODEL_PROMPT
    assert [classify_intent(x) for x in [" C ", "r", "X"]] == ["confirm", "reschedule", "cancel"]
    assert classify_intent("extravagant") == "other"
