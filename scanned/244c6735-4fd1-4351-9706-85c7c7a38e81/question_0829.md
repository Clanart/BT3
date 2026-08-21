# Q0829: webhook_job_klass: A path traversal / basename trick in `Pathname(path).basename` ...

## Question
Can an unprivileged attacker (only indirectly: the registered path -> job class name resolution) reach `webhook_job_klass / webhook_job_klass_name / path` in lib/shopify_app/managers/webhooks_manager.rb via webhook registration driven by configured paths (reachable during attacker-initiated install), supplying a path traversal / basename trick in `Pathname(path).basename` altering the resolved job, so that webhook path->job resolution must not be steerable to an unintended handler is violated, leading to unexpected job invocation (only if attacker-reachable with impact)? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/managers/webhooks_manager.rb — `webhook_job_klass / webhook_job_klass_name / path`
- Entrypoint: webhook registration driven by configured paths (reachable during attacker-initiated install)
- Attacker controls: only indirectly: the registered path -> job class name resolution — specifically a path traversal / basename trick in `Pathname(path).basename` altering the resolved job.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: webhook path->job resolution must not be steerable to an unintended handler
- Expected Immunefi impact: unexpected job invocation (only if attacker-reachable with impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
