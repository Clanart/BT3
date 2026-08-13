### Title
Byte-prefix truncation in `cap_diff_for_prompt` predictably drops trailing high-risk diff lines, allowing padding-based bypass of the security review - (File: `plugins/security-guidance/hooks/review_api.py`)

### Summary
`cap_diff_for_prompt` truncates any file whose diff content exceeds `DIFF_PER_FILE_BYTES` (default 80,000) by keeping only `content[:DIFF_PER_FILE_BYTES]` and appending a truncation marker, and truncates/omits later files once the running total exceeds `DIFF_TOTAL_BYTES` (default 400,000). Because this is a deterministic byte-position cut with no risk-aware reordering of lines within a file, an attacker who controls the position of dangerous code inside an oversized diff (by padding it with benign content earlier in the same file, or by ordering files so the malicious one is late in the list) can reliably push the dangerous `+` lines past the cutoff so they never reach the review-model prompt.

### Finding Description
`cap_diff_for_prompt` ( [1](#0-0) ) is consumed by `build_investigate_prompt` ( [2](#0-1) ), which builds the prompt for the agentic commit/push security reviewer (Stage-1 "investigate" pass). The equivalent `_cap_files_for_prompt` in `llm.py` ( [3](#0-2) ) is used for the single-shot Stop-hook diff review and the agentic review path ( [4](#0-3) ).

Both functions truncate per-file content with a **blind prefix cut**: `content[:DIFF_PER_FILE_BYTES]` keeps only the first N bytes and drops everything after, and the total-byte cap similarly starts omitting/truncating files once the running total is exceeded, in list order. Neither function reorders or scores lines/files by security relevance before cutting — that risk-aware step (`_prioritize_diff_files` in `gitutil.py`, [5](#0-4) ) only operates at the whole-file selection stage (which files are included at all), not at the byte-truncation stage within a kept file's content.

Because Claude Code's diff review is meant to catch dangerous code the agent (or an attacker via prompt injection driving the agent) introduces, an attacker-influenced diff can:
1. Place a large benign blob (comments, generated-looking padding, boilerplate) earlier in a single file's diff so that a genuinely dangerous `+` line (e.g. `eval(user_input)`, a new SSRF sink, a removed authz check) falls past byte offset 80,000 in that file's diff text.
2. Rely on `cap_diff_for_prompt`/`_cap_files_for_prompt` to silently cut the file there and append only a generic `"... [truncated by security-guidance: file exceeds per-file byte cap]"` marker — the dangerous line is simply absent from what the reviewer LLM sees.
3. For multi-file diffs, order so the dangerous file's content is appended after `DIFF_TOTAL_BYTES` has already been consumed by earlier (attacker-added) files, causing it to be entirely replaced by `"[omitted by security-guidance: total diff byte cap reached]"`.

This is deterministic and repeatable — it is not a heuristic mis-score but a guaranteed byte-offset property, so it "consistently" drops the targeted lines rather than doing so probabilistically.

### Impact Explanation
This breaks the review pipeline's core guarantee that changed (`+`) lines are shown to the reviewing model. Since the Stop-hook / commit / push LLM review is a security control intended to flag exactly this class of dangerous code before it lands, an attacker who can influence diff shape (via prompt-injected instructions that get Claude to write oversized files, or via a crafted PR/commit that Claude then reviews) can make the truncation silently exclude the dangerous change from ever being scored, effectively routing around the review boundary. This matches "Security-control bypass that silently disables or routes around blocking, review, or permission boundaries." The layer-1 regex pattern rules operate on separate, non-diff-review paths and may still catch some known patterns, but the LLM-based review (the primary defense for broader/novel vulnerability classes covered in the extensive prompt at [6](#0-5) ) is bypassed for content beyond the cap.

### Likelihood Explanation
Feasible with no special privilege: the attacker only needs to influence the content/size/order of a diff that ends up passed through `cap_diff_for_prompt`/`_cap_files_for_prompt` — reachable via any flow where repository content or injected instructions cause Claude to write a large file with the dangerous change placed after ~80KB of prior content, or cause many files to be touched so a targeted file falls past the 400KB total budget. The behavior is deterministic (byte-offset based), so it is 100% repeatable once the padding size is known, and the defaults (`DIFF_PER_FILE_BYTES=80000`, `DIFF_TOTAL_BYTES=400000`) are documented/predictable.

### Recommendation
Make the per-file and total truncation risk-aware instead of a blind byte-prefix cut:
- Within a single file's diff, prioritize retaining `+`/`-` hunks (and especially hunks matching the same security-risk heuristics used by `_prioritize_diff_files`) over unchanged context lines when trimming to the byte budget, rather than always keeping only the head.
- When omitting/truncating files for the total-byte cap, sort files by the same risk score used in `_prioritize_diff_files` before applying the cap, so higher-risk files are less likely to be the ones truncated/omitted.
- Alternatively, chunk oversized files into multiple review passes instead of dropping the tail, so no `+` line is ever excluded from review.

### Proof of Concept
Unit test in `plugins/security-guidance/hooks/` targeting `cap_diff_for_prompt`:
```python
from review_api import cap_diff_for_prompt, DIFF_PER_FILE_BYTES

def test_truncation_drops_trailing_dangerous_line():
    padding = "+ // benign filler line\n" * (DIFF_PER_FILE_BYTES // 24 + 10)
    dangerous_line = "+eval(user_input)  # DANGEROUS\n"
    content = padding + dangerous_line
    files = [("app/handler.py", content)]

    capped, dropped = cap_diff_for_prompt(files)

    fp, capped_content = capped[0]
    assert dropped > 0
    # The dangerous line is pushed past the byte cap and is silently dropped —
    # the reviewer prompt never contains it.
    assert "eval(user_input)" not in capped_content
    assert "truncated by security-guidance" in capped_content
```
Expected result: the assertion `"eval(user_input)" not in capped_content` passes, confirming the dangerous `+` line built into `build_investigate_prompt`'s output never reaches the review model, demonstrating the reliable bypass.

### Citations

**File:** plugins/security-guidance/hooks/review_api.py (L31-64)
```python
def cap_diff_for_prompt(
    files: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """Cap per-file and total diff bytes; return (capped_files, bytes_dropped).

    Truncation markers are written inside the content so the reviewer
    knows the file is incomplete.
    """
    out: list[tuple[str, str]] = []
    dropped = 0
    total = 0
    for fp, content in files:
        if len(content) > DIFF_PER_FILE_BYTES:
            dropped += len(content) - DIFF_PER_FILE_BYTES
            content = (
                content[:DIFF_PER_FILE_BYTES]
                + "\n... [truncated by security-guidance: file exceeds per-file byte cap]"
            )
        room = DIFF_TOTAL_BYTES - total
        if room <= 0:
            dropped += len(content)
            out.append(
                (fp, "[omitted by security-guidance: total diff byte cap reached]")
            )
            continue
        if len(content) > room:
            dropped += len(content) - room
            content = (
                content[:room]
                + "\n... [truncated by security-guidance: total diff byte cap reached]"
            )
        total += len(content)
        out.append((fp, content))
    return out, dropped
```

**File:** plugins/security-guidance/hooks/review_api.py (L156-176)
```python
def build_investigate_prompt(
    touched_paths: list[str],
    diff_files: list[tuple[str, str]],
    *,
    context_note: str = "",
) -> str:
    capped, _ = cap_diff_for_prompt(diff_files)
    diff_text = "\n\n".join(
        f"=== DIFF: {fp} ===\n{content}" for fp, content in capped
    )
    return (
        "Review this change for security vulnerabilities.\n\n"
        "Changed files (you may Read these and any other file in the repo):\n"
        + "\n".join(f"  - {p}" for p in touched_paths[:50])
        + context_note
        + "\n\nUnified diff (only + lines are new):\n\n"
        + diff_text
        + extensibility.guidance_block()
        + "\n\nInvestigate per the method in your instructions, then return "
        "the findings list."
    )
```

**File:** plugins/security-guidance/hooks/llm.py (L158-183)
```python
def _cap_files_for_prompt(files):
    """Cap per-file and total content bytes before they're packed into the
    review prompt. Returns the capped (path, content) list. Sets module-level
    _last_review_truncated_bytes to the number of bytes dropped (0 if none) so
    the Stop hook can emit a `diff_truncated` metric. Truncation markers are
    written INSIDE the content so the reviewer knows the file is incomplete.
    """
    global _last_review_truncated_bytes
    _last_review_truncated_bytes = 0
    out = []
    total = 0
    for fp, content in files:
        if len(content) > DIFF_PER_FILE_BYTES:
            _last_review_truncated_bytes += len(content) - DIFF_PER_FILE_BYTES
            content = content[:DIFF_PER_FILE_BYTES] + "\n... [truncated by security-guidance: file exceeds per-file byte cap]"
        room = DIFF_TOTAL_BYTES - total
        if room <= 0:
            _last_review_truncated_bytes += len(content)
            out.append((fp, "[omitted by security-guidance: total diff byte cap reached]"))
            continue
        if len(content) > room:
            _last_review_truncated_bytes += len(content) - room
            content = content[:room] + "\n... [truncated by security-guidance: total diff byte cap reached]"
        total += len(content)
        out.append((fp, content))
    return out
```

**File:** plugins/security-guidance/hooks/llm.py (L799-872)
```python
IMPORTANT vulnerability categories to check:

**Command Injection**: Is user input passed to shell commands or system exec calls? In Go, exec.Command("sh", "-c", userInput) is injectable. Even exec.Command("cmd", userArg) can be dangerous if userArg isn't validated (e.g., a hostname could contain shell metacharacters in some contexts). Safe: pass each argument separately without invoking a shell, AND validate the input format.

**Path Traversal**: Is user input used to construct file paths? Key insight: filepath.Join() in Go does NOT prevent path traversal — filepath.Join("/var/log", "../../etc/passwd") returns "/etc/passwd". Same for Python's os.path.join() and Java's Paths.get().resolve(). CRITICAL: `path.resolve()`/`filepath.Clean()`/`normalize()` are LEXICAL — they collapse `..` but do NOT dereference symlinks, so `startsWith(baseDir)` after them is symlink-bypassable. Call `fs.realpathSync()`/`os.path.realpath()`/`filepath.EvalSymlinks()` FIRST, then check the result starts with the realpath of baseDir.

**SQL Injection**: Is user input concatenated into SQL queries instead of using parameterized queries? This includes f-string interpolation (e.g., `f"WHERE name = '{{user_input}}'"`) and string concatenation (e.g., `"WHERE name = '" + user_input + "'"`). Even if input appears to be validated upstream, use parameterized queries. In Python: `cursor.execute('WHERE name = %s', (user_input,))`. In Go: `db.Query('WHERE name = $1', userInput)`.

A NEW security-gate parameter (group/role/tool/permission/scope) is safe only if (a) the gate is enforced unconditionally, OR (b) when its enabling condition is False the function raises/denies. If execution can continue past the new gate unchecked, flag fail-open — a later check may be vacuous when the new gate was the caller's only constraint.

**Authorization (IDOR / scoping / visibility)**: A handler that returns or modifies a tenant-, owner-, role-, or visibility-scoped resource MUST verify the requester is in that scope. Missing-authz patterns: `findById(id)` / `Model.objects.get(id=id)` without an ownership check; `Model.objects.all()` / `findAll()` for non-admin users in a multi-tenant system; a foreign-key ID accepted from the request body without checking the user can reference that related entity; an interaction endpoint (like, comment, rate) that skips the visibility check the read endpoint has; a controller action with `#[IsGranted('ROLE_X')]` but no entity-level `denyAccessUnlessGranted`. The check may be a decorator, a WHERE-clause filter, an ownership comparison, or a voter — its ABSENCE on a scoped resource is the  ... (truncated)

**Secrets/PII in Logs, URLs, or Errors**: Any sink that persists or transmits values an observer of logs/URLs/errors shouldn't see. Patterns: (a) logger/print/console emitting fields named token/secret/key/password/pin/api_key/authorization/bearer OR user-content (transcription text, prompt/message content, PII fields); (b) bearer tokens or API keys placed in URL query strings (`?key=`, `?token=`, `?access_token=`) — leaks to access logs/referer/history; (c) `str(exc)`/`repr(exc)`/`fmt.Errorf("...%s", respBody)`/`traceback.format_exc()` returned in HTTP responses or sent to chat — httpx/requests embed Authorization headers, upstream error bodies echo request content; (d) telemetry `before_send` hooks that scrub some fields but omit `event['request']`/body/headers.

**Unsafe Deserialization**: Untrusted bytes/paths reaching pickle deserialization including via wrappers — `pickle.load`/`pickle.loads`, `torch.load` or `.torch_load()` without `weights_only=True`, `yaml.load` without `SafeLoader`, `joblib.load`, `cloudpickle.load`/`.cloudpickle_load()`, `marshal.loads`, PHP `unserialize`, Java `ObjectInputStream`. Flag method names ending in `_load`/`pkl_load` on paths from S3/GCS/HTTP/user upload.

**TLS Verification Disabled / Plaintext Transport**: An explicit literal that disables transport encryption or peer-cert validation for a non-loopback connection. Client-side: Python `requests.*(verify=False)` / `httpx.Client(verify=False)` / `ssl._create_unverified_context()`; Go `tls.Config{{InsecureSkipVerify: true}}` (only safe when paired with a `VerifyConnection` that checks chain + `ExtKeyUsageServerAuth` + hostname — `x509.ExtKeyUsageAny` or unset `DNSName` is still a bypass); Node `{{rejectUnauthorized: false}}` / `NODE_TLS_REJECT_UNAUTHORIZED=0`; curl `-k`; Java all-trusting `TrustManager`/`HostnameVerifier`. Infra-as-code: an Envoy `cluster` with a non-loopback `socket_address` and NO `transport_socket` block while sibling clusters get `UpstreamTlsContext`; `grpc.insecure_channe ... (truncated)

**SSRF (Server-Side Request Forgery)**: A user-influenceable URL/host/path reaching an outbound fetch — `requests.get`/`httpx`/`aiohttp`/`urllib`/`fetch`/`axios`/`http.Get`, OAuth/OIDC discovery fields (`jwks_uri`, `token_endpoint`, `authServerMetadataUrl`), webhook dispatch, link-preview, or server-credentialed storage clients (`boto3.get_object`, `gcs.Blob.from_string`) on a bucket/key from an attacker-authored manifest. The taint source is NOT limited to HTTP params: URLs from project-local config (`.mcp.json`, `.vscode/settings.json`, `package.json`, workspace YAML in a cloned repo) and manifest/checkpoint files an attacker wrote earlier are attacker-controlled. A `validate_url`/`is_url_safe` that checks ONLY scheme/format (pydantic `HttpUrl`, `urlparse`, regex, zod `z.string()`) or co ... (truncated)

**Argument Injection (argv flag smuggling)**: User input as a positional argv element — `spawn(bin,[...])`, `execFile`, `subprocess.run([...])`, `exec.Command(bin, args...)` — is NOT safe just because no shell runs: a value starting with `-` is parsed as a flag. Exec-capable flags: ripgrep `--pre=CMD`, git `--upload-pack=CMD`/`-c core.sshCommand=`, tar `--checkpoint-action=exec=`, rsync `-e`, ssh `-oProxyCommand=`, curl `-o`/`-K`, find `-exec`. Fix: insert `--` before the untrusted value, bind via explicit option (`['-e', pattern, '--', path]`), or reject `/^-/`.

**OAuth/OIDC Flow Weaknesses**: (a) **Forgeable state** — an OAuth callback's `state` is CSRF-protective ONLY if unguessable AND bound to the session (compared against a cookie/server-session, or HMAC-verified). A `state` decoded as plain base64 JSON (`JSON.parse(Buffer.from(state,'base64url'))`, `json.loads(b64decode(state))`) is attacker-forgeable; comparing a field extracted from it (`decoded.email === identity.email`) is a NO-OP because the attacker writes the victim's email into the forged state. Flag callbacks decoding `state` without `crypto.createHmac` verify, `cookies.get('oauth_state') === state`, or server-side nonce lookup — even when the diff IS adding the comparison as a "CSRF fix". (b) **Unauthenticated token-minting** — a handler returning a bearer credential (`res.json({{s ... (truncated)

**XSS — Autoescape Off / Incomplete or Wrong Escaper**: (a) `jinja2.Environment()`/`jinja2.Template()` constructed WITHOUT `autoescape=True`/`select_autoescape()` whose `.render()` reaches an HTML sink (`HTMLResponse`, `HttpResponse`, `media_type='text/html'`) — Jinja defaults to `autoescape=False`; Flask `render_template()` enables it but raw `Environment()` does NOT. Same: Go `text/template` (vs `html/template`) to `http.ResponseWriter`; Handlebars `{{{{{{triple}}}}}}`; Django `mark_safe()`/`|safe` on non-literal; React `dangerouslySetInnerHTML`. (b) The `div.textContent=s; return div.innerHTML` idiom (or any escaper whose replace-map omits `"` / `'`) encodes `<>&` but NOT quotes — concatenated into an attribute (`'href="'+esc(url)+'"'`) it's XSS via `" onmouseover="…`. A protocol allowl ... (truncated)

**Sibling Validator/Sanitizer Asymmetry**: A diff where ONE field/argument receives a security refinement (regex/`.refine()`/sanitizer like `escapeHtml`/`stripBidiChars`/`DOMPurify.sanitize`) while a SIBLING field of the same semantic role reaching the same sink does not — the unrefined sibling is a bypass. The `+` line adding the refinement to one place is the cue: check every sibling.

**Orchestrator Template Injection (Airflow/Argo/Tekton)**: Airflow `{{{{ run_id }}}}`/`{{{{ dag_run.conf[...] }}}}`/`{{{{ params.* }}}}`, Argo `{{{{workflow.parameters.*}}}}`, or Tekton `$(params.*)` rendered into a shell string (`bash_command=`, `cmds=["bash","-c", ...]`, `script:`) — these are user-settable via the trigger API. Fix: pass as a separate argv element or env var. Do NOT flag scheduler-only macros like `{{{{ ds }}}}`.

**SSRF URL-Allowlist Bypass**: Host allowlists are bypassable via: (a) USERINFO — `url.startswith(allowed_prefix)` or comparing `urlparse().netloc`/`url.host` (which include `user:pass@`) lets `https://trusted.com@evil.com/x` through; compare ONLY `urlparse(u).hostname` / `new URL(u).hostname` / `u.Hostname()`. (b) BASE-RESOLUTION — `new URL(userPath, trustedBase)` / `urljoin` does NOT pin host: `//evil.com/x` is protocol-relative, absolute `http://evil.com` ignores base; check `result.hostname === expectedHost` AFTER resolution. (c) STRING-SUFFIX — `host.endswith('.trusted.com')` on a value later interpolated into `f"https://{{host}}"` passes `evil.com/.trusted.com` and `evil.com#.trusted.com`. (d) NORMALIZATION — missing `.lower().rstrip('.')` lets `Trusted.COM.` slip; falsy-netloc short ... (truncated)

**XXE / XML Entity Expansion**: Untrusted XML (uploaded .docx/.xlsx/.pptx/.svg, SOAP/SAML bodies, feed/webhook payloads, OOXML extracted from a zip) parsed with Python stdlib `xml.etree.ElementTree`, `xml.dom.minidom.parse`/`parseString`, `xml.sax.make_parser`, or `xml.dom.pulldom` — these do NOT disable DTDs or external entities, so `<!ENTITY x SYSTEM "file:///etc/passwd">` reads local files and a billion-laughs entity bomb DoS's the process. Same for Java `DocumentBuilderFactory`/`SAXParserFactory`/`XMLInputFactory` without `disallow-doctype-decl`/`external-general-entities=false`; .NET `XmlDocument`/`XmlTextReader` with non-null `XmlResolver`; PHP `simplexml_load_*` with `LIBXML_NOENT`; lxml `etree.parse` with `resolve_entities=True`. Fix: Python → swap import to `defusedxml.*`; Java →  ... (truncated)

**Substring/Unanchored Allowlist Bypass**: A security gate — allowlist, host/origin check, redirect-target validation, or SIEM/detection-rule exclusion — that matches by substring (`allowed in value`, `value.includes(allowed)`, `strings.Contains`, unanchored `re.search`) or unanchored prefix/suffix (`value.startswith("https://trusted.com")` with no trailing `/`; `value.endswith("trusted.com")` with no leading `.`) is bypassable: `trusted.com.evil.com`, `eviltrusted.com`, `evil.com/?x=trusted.com`. URL string-match on RAW `requestURI`: `/proxy/exec?_=/proxy/metrics` ends with `/proxy/metrics`; `/public/../admin` contains `/public/`. ALSO denylist alias bypass: regex blocks one literal form (`gpgsign\\s+false`, `javascript:`, `localhost`) where consumer accepts aliases (`=0`/`=no`/`=off`, `J ... (truncated)

**XSS via Manual HTML/Markdown Building**: Code assembling HTML by string formatting — `format!("<a href='{{x}}'>")`, `f"<div>{{val}}</div>"`, `fmt.Fprintf(w, "<span>%s</span>", v)`, `"<li>" + s + "</li>"` — is XSS at EVERY interpolated `{{var}}` lacking escape. INCONSISTENT: function calls `html.escape()` on SOME fields but interpolates others raw — audit each `{{...}}` individually; one `html.escape` nearby is NOT proof of safety. ATTRIBUTE-CONTEXT: data concatenated into quoted attribute (`'<a href="' + x + '">'`) is XSS unless escaper encodes `"` AND `'`; the `div.textContent=s; div.innerHTML` trick and `.replace(/[<>&]/g,...)` escape only `< > &` — NOT quotes; `[x](https://a" onmouseover=alert(1))` breaks out of `href`. MARKDOWN: `<MDEditor.Markdown source={{x}}>`, `react-markdown` wi ... (truncated)

**Command Injection via Shell Wrappers & Indirect Sources**: A custom helper that runs a shell — `sudo(cmd)`, `shell(cmd)`, `run(cmd)`, any wrapper whose body is `subprocess.run(cmd, shell=True)` / `Popen(["sh","-c",cmd])` — is the SAME sink as `os.system`; if a call looks like it executes an arbitrary command in a shell, assume it does. Any f-string/`+` building its argument from a non-literal is injectable. Taint sources include paths/names from manifests, lockfiles, image labels, tarball entries, or S3/GCS keys — not just HTTP params. `Path(x).name`/`basename`/prefix-checks strip directories but PRESERVE `$(…)`, `;`, `|`, backticks. Fix: `shlex.quote()` every segment, or pass an argv list without a shell.

**Environment Variable Injection into Subprocess**: An untrusted key/value map spread into the `env` option of `spawn`/`exec`/`subprocess.Popen`/`exec.Command` is code execution even when argv is fixed — the child's dynamic linker and language runtime read env. Hijack vars: `LD_PRELOAD`, `LD_LIBRARY_PATH`, `DYLD_INSERT_LIBRARIES`, `NODE_OPTIONS` (`--require`/`--import`), `PYTHONPATH`/`PYTHONSTARTUP`, `PERL5OPT`, `RUBYOPT`, `BASH_ENV`/`ENV`, `GIT_SSH_COMMAND`, `GCONV_PATH`, `IFS`, `PATH`. Shape: `spawn(cmd, args, {{env: {{...process.env, ...untrusted}}}})`, `Popen(..., env={{**os.environ, **untrusted}})`. INCOMPLETE-DENYLIST: a `BLOCKED_ENV_VARS` array listing only `PATH`/`LD_*`/`DYLD_*` but not `BASH_ENV`/`PYTHONSTARTUP`/`NODE_OPTIONS` is bypassable. INHERITED-LEAK: `process.env.SECRET = t ... (truncated)

**Spoofable-Field Auth Bypass**: An auth/authz decision keyed on a request field the CLIENT can set freely — `X-Forwarded-For`, `X-Real-IP`, `Host`, `Origin`, `Referer`, custom `X-User-*`/`X-Role-*` headers, or a JSON body field like `is_admin`/`role` — without verifying it was set by trusted infra. ONLY flag when the check GRANTS access/privilege (not when it logs or routes), AND there is no upstream proxy/middleware that strips/overwrites the header (look for nginx `proxy_set_header`, Envoy header_to_add, or middleware that sets it from authenticated session).

**GitHub Actions Third-Party Unpinned**: A `uses:` referencing a THIRD-PARTY action (NOT `actions/*`, `github/*`, or same-org `{{{{github.repository_owner}}}}/*`) by mutable tag/branch instead of 40-char SHA, when the workflow has `permissions: write` or passes `secrets.*`. Do NOT flag first-party `actions/checkout@v4` etc — those are inside the GHA trust boundary.


**Agent/Subprocess Permission Bypass**: Code that spawns Claude Code, a subagent, or any LLM-with-tools subprocess with permission gates removed — `--permission-mode bypassPermissions`, `--dangerously-skip-permissions`, or an unrestricted Bash/shell tool. Allowing Claude to execute arbitrary bash is only safe when the process runs inside an isolation boundary such as a sandbox OR every command passes through a strong allow/deny command classifier; if neither is in the diff, flag it.

**Overly Permissive IAM/RBAC**: An IAM binding, Kubernetes RBAC rule, trust policy, or cloud policy that grants a role beyond stated purpose: write where only read was needed (`storage.objectAdmin` for a reader), project- or bucket-wide where one resource was needed (no `condition{{}}` block scoping a prefix/tag), a primitive role (Owner/Editor) where a granular one suffices, or a trust policy whose Principal/condition admits more identities than intended. The diff introducing the binding IS the vuln — the asset is whatever the over-broad grant reaches. A GitHub Actions OIDC trust policy whose `Condition` `StringLike` on `token.actions.githubusercontent.com:sub` ends in `:*` (e.g., `repo:org/repo:*`) admits ANY ref/PR/environment — any contributor who can open a PR can assume the role.

**Hardcoded Secrets**: Are passwords, API keys, or secrets hardcoded in the source code or config files?

**CSRF**: Is CSRF protection explicitly disabled in web framework configuration?

**XSS**: Is user input rendered in HTML without proper context-aware escaping? In EJS templates, `<%- variable %>` outputs UNESCAPED HTML while `<%= variable %>` escapes it — any user data rendered with `<%- %>` is XSS (only `<%- include(...) %>` is safe). IMPORTANT: `html.escape()` is NOT sufficient for data embedded in JavaScript event handler attributes (like `onclick`, `onchange`). The browser HTML-decodes attribute values before executing JavaScript, so `&#x27;` becomes `'` again. For JavaScript contexts, use `json.dumps()` or `JSON.stringify()` to properly escape values.

**Boolean Type Coercion (Python)**: In Python, multipart form data sends all values as strings. `bool("false")` returns `True` because any non-empty string is truthy. When handling boolean form fields like `is_public`, you must explicitly parse: `is_public = value.lower() in ('true', '1', 'yes')`. Simply doing `is_public = request.form.get('is_public', True)` or `is_public = bool(request.form.get('is_public'))` is INSECURE because the string "false" evaluates to True.

**Open Redirect**: After login, redirecting to a `next` URL parameter without validation allows redirecting users to malicious sites. In Python/Flask: `redirect(request.args.get('next'))` is ALWAYS vulnerable. In Django: `redirect(request.GET.get('next'))` is ALWAYS vulnerable. Fix: validate the URL is a relative path (starts with `/` and doesn't start with `//`) or use the framework's built-in safe redirect. Django: use `url_has_allowed_host_and_scheme(url, allowed_hosts={{request.get_host()}})`. Flask: check `url.startswith('/') and not url.startswith('//')`.

**Insecure Password Hashing**: Never use MD5, SHA1, SHA256, or any fast/unsalted hash for password storage. Use bcrypt, scrypt, argon2, or PBKDF2. In Python: use `werkzeug.security.generate_password_hash()` or `bcrypt.hashpw()`. In Django: use `User.objects.create_user()` which handles hashing automatically.

**Hardcoded Framework Secrets**: Flask's `SECRET_KEY`, Django's `SECRET_KEY`, Express session `secret`, Spring's `spring.datasource.password`, and `DEBUG = True` must not be hardcoded with static strings. Read from environment variables: `os.environ.get('SECRET_KEY', os.urandom(32))`, `process.env.SESSION_SECRET`, `${{DB_PASSWORD}}`. A static/hardcoded string is INSECURE regardless of its complexity.

**Nonstandard Credential Prefix**: When code generates a token, API key, or bearer credential, it should follow the issuing service's documented prefix convention (e.g. `sk-` for OpenAI/Anthropic-style API keys, `ghp_` for GitHub, `AKIA` for AWS). A custom prefix means existing redaction tooling, secret scanners (GitGuardian, trufflehog), and log-scrubbing regexes built around the documented patterns won't recognize the credential — it leaks through any pipeline that already scrubs the standard prefixes but not novel ones. Only flag when: (1) the diff shows a token-generation site (template literal or format string assembling a prefix and random bytes), (2) the token is a real credential (not OAuth `state`, CSRF token, or similar), (3) the prefix does not match the issuing service's docume ... (truncated)

**Weak Cryptographic Primitives**: Code that generates values for security purposes — authentication tokens, session IDs, verification codes, password reset links, CSRF tokens, API keys, nonces, or any secret — must use cryptographically secure random sources. Standard language random APIs (`random` module in Python, `Math.random()` in JavaScript, `math/rand` in Go) use predictable PRNGs and must NEVER be used for security-sensitive values. In Python use `secrets` module; in JavaScript use `crypto.randomBytes()` or `crypto.getRandomValues()`; in Go use `crypto/rand`. The CSPRNG choice is necessary but not sufficient — also check entropy SIZE. Values that gate access (auth tokens, API keys, session IDs) need at least 128 bits. Values with weaker security relevance — anything an attacker wou ... (truncated)

**Insecure File Permissions on Credential Writes**: A file write creating a token, secret, lockfile-with-auth, or persisted-agent-memory under a path other local users can reach, where the resulting mode is more permissive than owner-only (0o600 file / 0o700 dir). Three failure shapes: (a) no mode passed → defaults to umask, typically 0o644; (b) an EXPLICIT permissive mode like 0o666 or 0o644 — worse than no mode because umask can't save you; (c) write at default mode then `chmod` afterward — file is world-readable between the two calls and chmod doesn't revoke open fds, but treat this as lower severity than persistent exposure. On multi-user hosts (devboxes, CI runners, Docker with permissive umask, shared compute) the gap between intended-mode and actual-mode is a credential-disclosure → ... (truncated)

**Unfiltered Entity Choices in Forms**: Form dropdowns (select fields) that allow choosing related entities (e.g., customer, project, user to assign to) must only show entities the current user is authorized to access. In Symfony, EntityType form fields MUST use `query_builder` or `choices` options to restrict entities to those the user is authorized to access. Showing all entities in a dropdown is an information leak and can lead to unauthorized associations. Server-side validation of submitted values is also required.

**Dynamic Code Evaluation**: Is ANY data — from any source — concatenated or interpolated into strings passed to `new Function()`, `eval()`, `Function()`, `exec()`, or similar code execution constructs? The data does NOT need to come from HTTP request input to be dangerous. Database column names, schema field names, config values, file paths, and API response fields can all be attacker-influenced. ANY string interpolation into code strings is equivalent to code injection. The PATTERN of string-building + code-evaluation is inherently dangerous regardless of the apparent trustworthiness of the data source. Fix: use safe property access (e.g., `obj[key]`, bracket notation, `array.reduce((o, k) => o[k], root)`, or a safe expression parser) instead of building code strings.
```

**File:** plugins/security-guidance/hooks/llm.py (L1139-1141)
```python
    diff_text = "\n\n".join(
        f"=== DIFF: {fp} ===\n{content}" for fp, content in _cap_files_for_prompt(diff_files)
    )
```

**File:** plugins/security-guidance/hooks/gitutil.py (L512-548)
```python
def _prioritize_diff_files(diff_files, cap):
    """When `diff_files` exceeds `cap`, return the top-`cap` by security
    relevance plus the count dropped. Otherwise return (diff_files, 0).

    Score = (risk_tokens_in_path, not_low_priority, added_lines). The
    added-lines proxy is `content.count('\\n+')` which counts diff additions
    cheaply without re-parsing hunks. This is a heuristic, not a guarantee —
    the goal is to review the likely-dangerous subset of an over-cap diff
    instead of reviewing nothing. Diffs that exceed the cap are typically
    large multi-file scaffolds, and the cross-file source→sink vulnerabilities
    in them concentrate in a handful of api/client/route files.
    """
    if len(diff_files) <= cap:
        return diff_files, 0

    def _score(item):
        fp, content = item
        low = fp.lower()
        # Prepend "/" so leading-slash patterns in _LOW_PRIORITY_PATH_TOKENS
        # match top-level dirs (git diff paths are repo-root-relative, e.g.
        # `migrations/001.py` not `/migrations/001.py`). Same trick as
        # _is_reviewable_source.
        low_slashed = "/" + low
        risk = sum(1 for t in _SECURITY_RISK_PATH_TOKENS if t in low)
        low_prio = (
            fp.endswith(_LOW_PRIORITY_SUFFIXES)
            or any(t in low_slashed for t in _LOW_PRIORITY_PATH_TOKENS)
        )
        # added_lines: count('\n+') over-counts by including '+++' header and
        # any literal '+' at line start in context, but it's a consistent
        # ordinal across files in the same diff which is all we need.
        added = content.count("\n+")
        return (risk, not low_prio, added)

    ranked = sorted(diff_files, key=_score, reverse=True)
    return ranked[:cap], len(diff_files) - cap

```
