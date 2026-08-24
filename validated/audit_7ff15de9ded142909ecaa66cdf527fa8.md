### Title
Forged `WWW-Authenticate` header from an attacker-controlled remote can spoof GitHub/GitHub Enterprise host detection and trigger a GitHub sign-in flow for that attacker host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Desktop's credential-helper trampoline decides whether a remote host should be treated as a GitHub/GitHub Enterprise endpoint based in part on a `WWW-Authenticate` (`wwwauth[]`) header value that Git forwards verbatim from the HTTP response of whatever remote the credential request is for. Because this header is fully controlled by the server on the other end of the connection, an attacker who controls a git remote (or a proxy in the request path) can forge `realm="GitHub"` and cause Desktop's `getEndpointKind` to classify their arbitrary host as `'enterprise'` without performing the normal network verification (`isGitHubHost`, which requires an actual `x-github-request-id` response header).

### Finding Description
`getEndpointKind` in [1](#0-0)  determines how a credential request should be routed:

```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
```

This check runs *before* the legitimate verification path, `isGitHubHost`, which performs an out-of-band `fetch` and only trusts the host if the server responds with the internal `x-github-request-id` header [2](#0-1) . The `wwwauth[...]` fields originate from Git's credential protocol, populated from the actual HTTP `WWW-Authenticate` response header sent by whatever server Git is talking to for that remote — i.e., it is attacker-controlled data whenever the attacker controls the remote/repository host or a proxy sitting on that connection.

Once `getEndpointKind` returns `'enterprise'` for a non-GitHub host, `getCredential` in the same file proceeds to look for a matching account and, finding none, calls `ui.promptForGitHubSignIn(endpoint)` [3](#0-2) , which starts Desktop's GitHub Enterprise sign-in/OAuth flow scoped to the attacker's host. This bypasses the only real host-authenticity check (`isGitHubHost`) that the rest of the code path relies on, and does so using a value that is not authenticated in any way — it's just a string in a response header the attacker's own server sent.

### Impact Explanation
This lets a malicious remote (or a MITM/proxy on an insecure connection) impersonate a "known GitHub host" to Desktop's internal logic, causing Desktop to initiate an account sign-in/OAuth binding flow for that attacker-controlled endpoint. This falls under the "unauthorized OAuth or account binding" impact category: the attacker did not need any local access, and simply hosting a git server that responds with a crafted header while the user performs a normal git operation (clone/fetch/push against a repo they were led to add) is enough to influence Desktop's endpoint-trust decision. It also affects `storeCredential`/`eraseCredential`, which use the same `getEndpointKind` result to decide whether the attacker host is treated as a `generic` credential (stored in the OS credential store) or a GitHub endpoint (bypassing the generic per-host password storage path).

### Likelihood Explanation
The attacker only needs to control (or host) the git remote and send a normal HTTP 401 with a spoofed `WWW-Authenticate: Basic realm="GitHub"` header during Git's credential-fill negotiation — no special network position or elevated privilege is required. Any repository/remote the user adds and interacts with (fetch/pull/push/clone) can trigger this since `getEndpointKind` is invoked on every credential-fill request. Because the `isGitHubHost` real check (the online verification) is only reached if the header-based shortcut doesn't match, an attacker can always take the shortcut.

### Recommendation
Do not trust the `wwwauth[]` realm string alone to promote a host to `'enterprise'` status. At minimum, use it only as a *hint* to prioritize/short-circuit the legitimate `isGitHubHost` network verification (which checks for the internal `x-github-request-id` response header) rather than as a substitute for it, so that account-linking/sign-in prompts and generic-vs-GitHub credential routing can't be decided purely from attacker-suppliable header text.

### Proof of Concept
1. User adds/clones a remote pointing at `https://evil.example.com/repo.git`, a server fully controlled by the attacker.
2. When Desktop's git process needs credentials for this remote, the attacker's server responds to the HTTP request with `WWW-Authenticate: Basic realm="GitHub"`.
3. Git's credential protocol forwards this as a `wwwauth[0]=...` field in the credential-fill request that reaches `createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind` [4](#0-3) .
4. `getEndpointKind` returns `'enterprise'` for `evil.example.com` without ever calling `isGitHubHost`.
5. Since no existing account matches `evil.example.com`, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, presenting a GitHub Enterprise sign-in/OAuth flow scoped to the attacker's host, or alternatively skips the "generic" credential storage/erase path that would otherwise be used for an unrecognized/non-GitHub host.

Note: I was unable to fully trace the exact downstream UI wording/OAuth handoff inside `trampoline-ui-helper.ts` (`promptForGitHubSignIn`) within the indexed portion of the codebase, so the precise dialog text and OAuth redirect target could not be fully verified — this should be confirmed against the full file before finalizing severity.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L109-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }

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

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```

**File:** app/src/lib/api.ts (L2467-2483)
```typescript
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
