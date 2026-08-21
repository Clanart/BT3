# Q2012: webhook_job_klass: A path traversal / basename trick in `Pathname(path).basename` ...

## Question
Can an unprivileged attacker (only indirectly: the registered path -> job class name resolution) reach `webhook_job_klass / webhook_job_klass_name / path` in lib/shopify_app/managers/webhooks_manager.rb via webhook registration driven by configured paths (reachable during attacker-initiated install), supplying a path traversal / basename trick in `Pathname(path).basename` altering the resolved job, so that webhook path->job resolution must not be steerable to an unintended handler is violated, leading to unexpected job invocation (only if attacker-reachable with impact)? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/managers/webhooks_manager.rb — `webhook_job_klass / webhook_job_klass_name / path`
- Entrypoint: webhook registration driven by configured paths (reachable during attacker-initiated install)
- Attacker controls: only indirectly: the registered path -> job class name resolution — specifically a path traversal / basename trick in `Pathname(path).basename` altering the resolved job.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: webhook path->job resolution must not be steerable to an unintended handler
- Expected Immunefi impact: unexpected job invocation (only if attacker-reachable with impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
