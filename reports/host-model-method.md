# Host-assistant blind smoke

Produced 20 case records: 13 administrative intent predictions and seven safety-prescreen interceptions (smoke-11 through smoke-17), with null model outputs for intercepted cases.

## Method

The available host assistant used its own reasoning against `evals/model_prompt.txt` and `evals/model_smoke_inputs.json` only. No labels, application runtime, secrets, or rule-based classifier were read or executed. Buyer utterances were treated as untrusted data, not instructions. The contact address in smoke-20 was disregarded as redacted contact information when inferring intent. No medical advice was generated. No dialog action was selected because inputs supplied no allowed actions.

The prompt does not specify a safety-result schema or define the meanings of H02/H03. Each record therefore uses the authorized envelope `{id, safety_route, model_output}`. `intercepted` records represent the requested prescreen routing, not actual wrapper execution; `not_intercepted` records contain only the required intent and handoff keys. Bare C/X and conflicting cancel/reschedule requests were treated as ambiguous without additional context.

This is a host-assistant smoke, not an API integration test, production provider call, or verification of the application safety wrapper. It reports no billed-token, latency, reference-label score, or model-quality claim. Structural completeness and JSON validity were checked separately; those checks did not generate predictions.
