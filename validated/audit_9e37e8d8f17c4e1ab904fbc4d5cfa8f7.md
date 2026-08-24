### Title
Git credential helper classifies arbitrary remotes as "GitHub Enterprise" based on attacker-controlled response headers, enabling credential/OAuth exfiltration to malicious hosts - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Sherlock report's broken invariant is: a security-critical value (the swap's `amountOutMinimum`) is derived *from the very data source being protected against* (the pool's own live, attacker-manipulable state) inside the same operation, so the "protection" is trivially defeated by whoever controls that data source. `getEndpointKind()` in GitHub Desktop's credential trampoline has the same structural flaw: it decides whether an arbitrary git remote should be trusted as a "GitHub"/"enterprise" endpoint by reading self-reported signals (`WWW-Authenticate` header, `x-github-request-id` header) returned by that very remote, then uses that classification to decide whether to prompt the user for GitHub sign-in / hand out GitHub credentials.

### Finding Description
When Git needs credentials for a remote, it invokes Desktop's credential helper, which calls `getEndpointKind()`: [1](#0-0) 

This loop inspects the `wwwauth[...]` values that git forwarded from the remote server's HTTP response and, if the value merely contains the substring `realm="GitHub"`, classifies the endpoint as `'enterprise'` — i.e. a trusted GitHub host. This value is 100% attacker-controlled: any HTTPS server the user's git operation talks to (a malicious clone URL, a compromised/attacker-controlled proxy, a redirected/rewritten remote, or a submodule remote pointing at a different host) can simply respond with `WWW-Authenticate: Basic realm="GitHub"` on a 401.

If no such header is present, the code falls back to `isGitHubHost(endpoint)`: [2](#0-1) 

This makes a request to the attacker's own host and trusts it if the response merely includes an `x-github-request-id` header — again a header the attacker's server can trivially forge, since it's their server answering the request.

Once `getEndpointKind()` returns `'enterprise'`, `getCredential()` treats the host as a genuine GitHub endpoint: [3](#0-2) 

If no existing account matches, Desktop invokes `ui.promptForGitHubSignIn(endpoint)` — Desktop's real GitHub-Enterprise sign-in UI — pointed at the attacker's endpoint. If the user completes sign-in (a very natural action, since the dialog looks identical to a legitimate GHE prompt triggered by a real corporate remote), any resulting token/credential exchange is driven by `apiEndpoint = getAPIEndpoint(endpoint)`, which is derived entirely from the attacker's host, not a vetted GitHub/GHE registry. This is analogous to the Swap bug: the guard (`endpointKind !== 'generic'`) that is supposed to gate whether Desktop treats a remote as GitHub-worthy is computed from data the attacker fully controls, so it provides no real protection — exactly as `amountOutMinimum` computed from the manipulable pool state provided no real slippage protection.

### Impact Explanation
An attacker who controls a git remote/proxy (e.g. a malicious clone URL a victim adds or is redirected to, a compromised HTTP proxy, or a spoofed submodule remote) can cause GitHub Desktop's credential trampoline to misclassify that remote as a trusted GitHub Enterprise endpoint. This can:
- Trigger Desktop's real GitHub sign-in flow against the attacker's endpoint, phishing the user into an OAuth/PAT exchange whose resulting token material is scoped/sent to the attacker's `apiEndpoint`.
- Cause the credential prompt UX to lend attacker infrastructure false legitimacy ("this looks like a normal enterprise sign-in").

This matches the valid-impact class: attacker controls a git remote/proxy response, resulting in unauthorized OAuth flow / credential exfiltration risk — without requiring local access, admin rights, or pre-existing compromise.

### Likelihood Explanation
Exploitation requires only that the victim perform a normal git operation (fetch/push/clone) against a remote the attacker controls or can respond as (e.g., after adding a remote from an untrusted source, or via a MITM-capable proxy/redirect in an enterprise network). No unnatural steps are needed beyond the credential prompt appearing, which is expected/normal Desktop behavior for enterprise hosts, making it easy to overlook.

### Recommendation
Do not derive trust classification for "is this a GitHub/GHE host" from headers returned by the untrusted remote itself. Trust decisions should be based on:
- An explicit allow-list of endpoints the user has configured/added as accounts (already partially done via `findGitHubTrampolineAccount`), and
- Independent verification (e.g., checking a pinned/known GHE endpoint registry or requiring explicit user confirmation with the exact hostname) rather than self-reported `WWW-Authenticate` realms or `x-github-request-id` headers, both of which are trivially spoofable by the very server being evaluated.
At minimum, before invoking `promptForGitHubSignIn`, surface the literal hostname prominently and require explicit user confirmation rather than silently treating header-based signals as sufficient proof of "GitHub-ness."

### Proof of Concept
1. Attacker stands up an HTTPS server and gets a victim to add it as a git remote (e.g. `git remote add origin https://attacker.example/foo/bar.git`) or to interact with a repo whose submodule/remote points there.
2. On any git operation requiring auth, when Git contacts the attacker server, the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Git forwards this via the trampoline `stdin` as a `wwwauth[...]` entry; `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'`: [4](#0-3) 
4. Since no account exists for `apiEndpoint` (derived from the attacker's host), `getCredential()` calls `ui.promptForGitHubSignIn(endpoint)`, presenting Desktop's native GitHub sign-in dialog scoped to the attacker's host: [5](#0-4) 
5. If the user proceeds (natural action, since the UI mirrors legitimate enterprise sign-in), the OAuth/token flow targets the attacker-controlled endpoint.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-165)
```typescript

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }
```

**File:** app/src/lib/api.ts (L2465-2483)
```typescript
  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
```
