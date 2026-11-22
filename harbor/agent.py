"""Bounded model-assisted administrative orchestration; all execution remains code-owned."""

from __future__ import annotations
import inspect
from .llm import ModelClient
from .policy import Privacy, safety_screen

PLANS = {
    "book": ["search_availability"],
    "reschedule": ["reschedule"],
    "cancel": ["cancel"],
    "confirm": ["confirm"],
    "faq": ["answer_faq"],
    "intake": ["send_intake_link"],
    "waitlist": ["join_waitlist"],
}
WRITES = {"hold_slot", "book", "reschedule", "cancel", "confirm", "join_waitlist", "send_intake_link"}


class AgentOrchestrator:
    def __init__(self, client: ModelClient):
        self.client = client

    async def run(
        self,
        utterance: str,
        safe_context: dict,
        tool_handlers: dict,
        conversation_id: str,
        *,
        known_personal_values: tuple[str, ...] = (),
        verified: bool = False,
        user_confirmed: bool = False,
        requested_action: str | None = None,
    ) -> dict:
        """Handlers are zero-argument closures bound by API to validated requests.

        Model supplies no tool arguments and cannot expand available tools. Explicit
        patient confirmation plus a verified session is required for every write.
        Result payloads are returned to the caller, never sent back to a model.
        """
        safety = safety_screen(utterance)
        if safety:
            return {"status": "handoff", "rule": safety.rule, "message": safety.message, "results": []}
        context = {
            k: v
            for k, v in safe_context.items()
            if k in {"service_key", "slot_ids", "missing_fields", "available_count"}
        }
        sanitized = Privacy.redact(utterance, known_personal_values)
        results = []
        try:
            classification = await self.client.call("intent", dict(context, utterance=sanitized), conversation_id)
            intent = classification["intent"]
            if classification["handoff"] or intent == "other":
                return self._handoff(results)
            plan = PLANS.get(intent, [])
            if requested_action:
                # Explicit action is supplied only by the trusted API, never inferred by model.
                if requested_action not in set(PLANS.get(intent, [])) | (
                    {"hold_slot", "book"} if intent == "book" else set()
                ):
                    return self._handoff(results)
                plan = [requested_action]
            for action in plan:
                if len(results) >= 7:
                    return self._handoff(results)
                if action not in tool_handlers:
                    return self._handoff(results)
                if action in WRITES and not (verified and user_confirmed):
                    return {
                        "status": "confirmation_required",
                        "intent": intent,
                        "message": "Verify your identity and explicitly confirm this action before proceeding.",
                        "results": results,
                    }
                decision = await self.client.call(
                    "dialog",
                    dict(context, intent=intent, verified=verified, allowed_actions=[action, "handoff"]),
                    conversation_id,
                )
                if decision.get("handoff") or decision.get("next_action") != action:
                    return self._handoff(results)
                value = tool_handlers[action]()
                if inspect.isawaitable(value):
                    value = await value
                results.append({"tool": action, "result": value})
            # A second planner step can end only; no repeat writes are available.
            decision = await self.client.call(
                "dialog",
                {"intent": intent, "verified": verified, "allowed_actions": ["finish", "handoff"]},
                conversation_id,
            )
            if decision.get("handoff") or decision.get("next_action") != "finish":
                return self._handoff(results)
            return {
                "status": "completed",
                "intent": intent,
                "message": "The administrative request has been processed. Review the returned details.",
                "results": results,
            }
        except Exception:
            # No exception text/raw intake/factors leaks to patient or model.
            return self._handoff(results)

    @staticmethod
    def _handoff(results):
        return {
            "status": "handoff",
            "message": "Staff assistance is required. No further automated action will be taken.",
            "results": results,
        }
