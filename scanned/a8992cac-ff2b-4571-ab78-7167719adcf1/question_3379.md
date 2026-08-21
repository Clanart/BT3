# Q3379: webhook_job_klass: A webhook path whose `classify`/`safe_constantize` resolves to ...

## Question
Can an unprivileged attacker (only indirectly: the registered path -> job class name resolution) reach `webhook_job_klass / webhook_job_klass_name / path` in lib/shopify_app/managers/webhooks_manager.rb via webhook registration driven by configured paths (reachable during attacker-initiated install), supplying a webhook path whose `classify`/`safe_constantize` resolves to an unintended job class, so that webhook path->job resolution must not be steerable to an unintended handler is violated, leading to unexpected job invocation (only if attacker-reachable with impact)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/managers/webhooks_manager.rb — `webhook_job_klass / webhook_job_klass_name / path`
- Entrypoint: webhook registration driven by configured paths (reachable during attacker-initiated install)
- Attacker controls: only indirectly: the registered path -> job class name resolution — specifically a webhook path whose `classify`/`safe_constantize` resolves to an unintended job class.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: webhook path->job resolution must not be steerable to an unintended handler
- Expected Immunefi impact: unexpected job invocation (only if attacker-reachable with impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
