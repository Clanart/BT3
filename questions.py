import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'Shopify/shopify_app'
# todo: the name of the repository
REPO_NAME = 'shopify_app'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Public auth entrypoints: login, OAuth callback, token exchange, logout
    # =================================================================================
    "app/controllers/shopify_app/sessions_controller.rb",
    "app/controllers/shopify_app/callback_controller.rb",
    "app/controllers/shopify_app/authenticated_controller.rb",
    "app/controllers/shopify_app/extension_verification_controller.rb",
    "app/controllers/shopify_app/webhooks_controller.rb",
    "config/routes.rb",

    # =================================================================================
    # Session gating concerns applied to every protected controller action
    # =================================================================================
    "app/controllers/concerns/shopify_app/ensure_has_session.rb",
    "app/controllers/concerns/shopify_app/ensure_installed.rb",
    "app/controllers/concerns/shopify_app/ensure_authenticated_links.rb",
    "app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb",

    # =================================================================================
    # Session token / OAuth authorization: shop resolution, redirects, CSRF, embedding
    # =================================================================================
    "lib/shopify_app/controller_concerns/login_protection.rb",
    "lib/shopify_app/controller_concerns/token_exchange.rb",
    "lib/shopify_app/controller_concerns/with_shopify_id_token.rb",
    "lib/shopify_app/controller_concerns/csrf_protection.rb",
    "lib/shopify_app/controller_concerns/embedded_app.rb",
    "lib/shopify_app/controller_concerns/redirect_for_embedded.rb",
    "lib/shopify_app/controller_concerns/frame_ancestors.rb",
    "lib/shopify_app/controller_concerns/sanitized_params.rb",
    "lib/shopify_app/controller_concerns/localization.rb",
    "lib/shopify_app/controller_concerns/ensure_billing.rb",

    # =================================================================================
    # HMAC / signature verification of untrusted inbound requests
    # =================================================================================
    "lib/shopify_app/controller_concerns/webhook_verification.rb",
    "lib/shopify_app/controller_concerns/app_proxy_verification.rb",
    "lib/shopify_app/controller_concerns/payload_verification.rb",

    # =================================================================================
    # Token acquisition and post-authentication side effects
    # =================================================================================
    "lib/shopify_app/auth/token_exchange.rb",
    "lib/shopify_app/auth/post_authenticate_tasks.rb",
    "lib/shopify_app/admin_api/with_token_refetch.rb",

    # =================================================================================
    # Session storage: where shop and user access tokens are persisted and looked up
    # =================================================================================
    "lib/shopify_app/session/session_repository.rb",
    "lib/shopify_app/session/session_storage.rb",
    "lib/shopify_app/session/shop_session_storage.rb",
    "lib/shopify_app/session/shop_session_storage_with_scopes.rb",
    "lib/shopify_app/session/user_session_storage.rb",
    "lib/shopify_app/session/user_session_storage_with_scopes.rb",
    "lib/shopify_app/session/in_memory_session_store.rb",
    "lib/shopify_app/session/in_memory_shop_session_store.rb",
    "lib/shopify_app/session/in_memory_user_session_store.rb",
    "lib/shopify_app/session/null_user_session_store.rb",

    # =================================================================================
    # Access scope reconciliation deciding when re-authorization is required
    # =================================================================================
    "lib/shopify_app/access_scopes/shop_strategy.rb",
    "lib/shopify_app/access_scopes/user_strategy.rb",
    "lib/shopify_app/access_scopes/noop_strategy.rb",

    # =================================================================================
    # Shop domain sanitization, configuration, engine wiring, logging of secrets
    # =================================================================================
    "lib/shopify_app/utils.rb",
    "lib/shopify_app/configuration.rb",
    "lib/shopify_app/engine.rb",
    "lib/shopify_app/errors.rb",
    "lib/shopify_app/logger.rb",

    # =================================================================================
    # Background managers acting with a stored shop token
    # =================================================================================
    "lib/shopify_app/managers/webhooks_manager.rb",
    "lib/shopify_app/managers/script_tags_manager.rb",
    "app/jobs/shopify_app/webhooks_manager_job.rb",
    "app/jobs/shopify_app/script_tags_manager_job.rb",
]


target_scopes = [
    "Critical. An unauthenticated attacker who can only send HTTP requests to a public route of an app built on this gem obtains a valid Shopify access token or session for a shop they do not own, by abusing shop-parameter handling, session id derivation, ID token verification, or token exchange in the login, callback, or token-exchange path.",
    "Critical. An unauthenticated attacker reaches a controller action protected by ensure_has_session, ensure_installed, or the token exchange concern without presenting a valid, correctly verified Shopify session token, or with a token whose signature, destination, audience, or expiry is not enforced.",
    "Critical. An attacker who controls one shop (or an arbitrary shop domain string) causes the app to load, overwrite, or act with another shop's or another user's stored session, through shop domain sanitization, session id or scope lookup, or session storage key collisions.",
    "Critical. An unauthenticated attacker forges a webhook, app proxy, or extension request that passes HMAC verification, through a comparison that is not constant-time, a wrong signing secret or payload, missing verification on a route, or signature parameter parsing that lets attacker-chosen data be excluded from the signed digest.",
    "Critical. An attacker exfiltrates a Shopify ID token, session token, access token, or host parameter to a domain they control by influencing a redirect, App Bridge redirect target, Content-Security-Policy frame-ancestors value, or return_to value that is not validated as a Shopify-owned or same-origin destination.",
    "High. An attacker bypasses CSRF protection or session invalidation so that state-changing app requests are accepted cross-origin, or a logged-out, uninstalled, or scope-revoked shop keeps a usable session or token.",
    "High. An unauthenticated attacker leaks a shop access token, ID token, or API secret into logs, error responses, or rendered views, or triggers unbounded work or an unhandled exception on a public route by supplying crafted shop, host, or webhook input.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one shopify_app target.

    ```
    target_file format:
    "'File Name: lib/shopify_app/controller_concerns/login_protection.rb -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact shopify_app target:

    {target_file}

    Project focus:
    shopify_app is the Rails engine Shopify apps use for authentication. Focus on session token (JWT) verification, token exchange, OAuth callback handling, shop domain sanitization, session storage and lookup keys, access scope checks, webhook/app-proxy/extension HMAC verification, CSRF protection, embedded-app redirects, and access token handling.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Ruby symbols (module, class, method, constant) when possible.
    * Attacker is unprivileged only: an anonymous internet user hitting the app's public routes, or a merchant who controls an unrelated shop and can craft any shop/host/HMAC/JWT parameter they send.
    * Attacker is NOT the app developer, host operator, or Shopify itself, and does NOT hold the app's API secret, a valid victim session token, or a leaked access token.
    * Ignore test files, mocks, test_helpers, generators and their templates, docs, CI config, and dependency-only issues.
    * Ignore issues that require misconfiguring the host app in ways the gem documents as unsupported.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target authentication bypass, session or token theft, cross-shop or cross-user session confusion, HMAC/JWT verification weakness, or token/secret exfiltration via redirect or log.
    * Every question must be testable by a controller test, integration test, unit test, or fuzz/differential test over crafted parameters.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authentication is exact: a request only gets a session if it presents a Shopify-signed ID token whose signature, `dest`, `aud`, `exp`, and `nbf` are verified against the configured API key and secret.
    * Session binding is exact: the shop and user a session is loaded, stored, or refreshed for is derived from verified token claims, never from an unverified `shop`, `host`, or `return_to` parameter.
    * Isolation holds: one shop or user can never read, overwrite, or act with another shop's or user's session, token, or scopes, including through session id or storage key collisions.
    * Signature verification is complete: every webhook, app proxy, and extension request is verified over the exact bytes Shopify signed, using constant-time comparison, before any side effect.
    * Secrets stay internal: access tokens, ID tokens, and API secrets never reach a redirect target, response body, log line, or frame-ancestor the attacker controls.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: controller/integration/fuzz PARAMETERS and assert AUTHENTICATION_ENFORCED, SESSION_BINDING, TENANT_ISOLATION, SIGNATURE_VERIFICATION, or SECRET_CONFINEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused shopify_app exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an anonymous HTTP client against the app's public routes, or a merchant controlling an unrelated shop, crafting arbitrary shop/host/JWT/HMAC parameters. No app secret, no valid victim session token, no leaked access token, no host or Shopify insider access.
- Reject anything requiring the app developer, hosting operator, physical/local network access, victim social engineering, or a documented misconfiguration of the host app.
- Reject anything that depends only on test/test_helpers/mock/generator-template/docs/CI files, dependency bugs alone, or best-practice cleanup without exploitable impact.
- Focus on real compromise paths: authentication bypass, session or access token theft, cross-shop or cross-user session confusion, forged webhook/app-proxy/extension requests, CSRF on state-changing actions, and secret exfiltration via redirect, header, response, or log.

## Validate
- Trace the exact reachable path from the attacker-controlled request (params, headers, JWT, HMAC, body) into the affected method.
- Check whether existing checks already stop it: ID token verification, `ShopifyApp::Utils.sanitize_shop_domain`, session id derivation, scope comparison, `ActiveSupport::SecurityUtils.secure_compare`, CSRF filters, or the calling controller's before_actions.
- Accept only concrete impact: unauthorized access to a shop's data or token, session takeover or confusion, accepted forged signed request, bypassed authentication or scope check, or leaked token/secret.
- Require exact file/method support and a reproducible controller/integration/unit PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Shopify HackerOne impact class]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Controller/integration/unit test or crafted request sequence with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for shopify_app security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject developer-only, host-operator-only, leaked-secret, physical/local-network, social-engineering, dependency-only, docs/style, generator-template, and test/mock/config-only issues.
- Reject missing headers, cookie flags, logout CSRF, self-XSS, scanner output, and theoretical claims with no demonstrated impact.
- Reject if the exploit needs the app's API secret, a valid victim session token, or an unsupported host-app configuration.
- A valid report must be triggerable by an anonymous HTTP client or an unrelated merchant against a default installation of this gem.
- The final impact must map to an in-scope class: authentication bypass, session or access token theft, cross-shop or cross-user data access, forged webhook/app-proxy/extension request accepted, CSRF with state change, or token/secret disclosure.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, method, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker HTTP request/params/JWT/HMAC -> trigger -> bad result.
4. Existing verification, sanitization, and before_action filters reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: controller/integration/unit PoC or exact request sequence against a default app.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an anonymous client or unrelated merchant trigger this without the app secret or a victim token?
- Does the code actually behave as claimed on the current version of the gem?
- Is the impact caused by this gem's code, not by the host app or a dependency alone?
- Is the auth bypass, token theft, cross-shop access, or forged-signature acceptance concrete, not hypothetical?
- Would a Shopify HackerOne triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Shopify bounty category]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible request sequence or controller/integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for shopify_app.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs in session token verification, token exchange, OAuth callback, shop domain sanitization, session storage lookup, access scope checks, webhook/app-proxy/extension HMAC verification, CSRF, or embedded-app redirects.
- Reject developer-only, host-operator-only, leaked-secret, dependency-only, test/generator-only paths, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable shopify_app path from an anonymous or unrelated-merchant HTTP request.
- Prove root cause with exact file/method support.
- Accept only concrete authentication bypass, session or token theft, cross-shop or cross-user access, accepted forged signed request, CSRF with state change, or secret disclosure.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
