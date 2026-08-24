### Title
Attacker-controlled `WWW-Authenticate` response spoofs GitHub host detection, forcing an unauthenticated remote to be treated as a trusted GitHub Enterprise endpoint and binding a sign-in flow to it - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Sherlock report's root cause is a broken invariant: the code assumes "condition A implies remote/cross-chain action B" and skips a safety step (refunding funds) whenever that assumption silently fails. The closest analog in GitHub Desktop is in the trampoline credential helper's `getEndpointKind()` function, which assumes that a `WWW-Authenticate` header value returned by the remote Git server is a trustworthy signal of "this is a genuine GitHub host." That header is fully attacker-controlled (it comes straight from the response of whatever remote/proxy Git is talking to), yet its content is used to short-circuit host classification and drive a user-facing GitHub sign-in flow bound to that attacker-controlled origin.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper (`createCredentialHelperTrampolineHandler` in `app/src/lib/trampoline/trampoline-credential-helper.ts:220-259`), passing along any `wwwauth[]` fields captured from the server's HTTP response headers. `getEndpointKind()` uses this attacker-supplied data as a "happy path" shortcut to decide whether the remote is GitHub Enterprise, before falling back to more reliable heuristics: [1](#0-0) 

```
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

Just like the CDS report's `_getDownsideFromCDS()` assumed "not enough local funds implies a cross-chain call is required," this code assumes "a header containing `realm="GitHub"` implies the remote is really GitHub." Neither assumption is validated against the actual authoritative state (in the DeFi case, actual on-chain balances; here, the actual identity/certificate of the remote). Any HTTP(S) server the user's Git client talks to — a malicious/compromised remote, a corporate MITM proxy, or a spoofed redirect target — can simply return `WWW-Authenticate: Basic realm="GitHub"` to force `getEndpointKind()` to return `'enterprise'`.

That classification feeds directly into `getCredential()`: [2](#0-1) 

If no existing account matches the (attacker's) API endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which for any non-`github.com` hostname calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` using the attacker-controlled `origin`, then shows the standard `SignIn` popup: [3](#0-2) 

This means a normal `git fetch`/`push`/`clone` against a repository whose remote is attacker-controlled (or sits behind an attacker-controlled proxy) can silently trigger Desktop's native "Sign in to GitHub Enterprise" UI bound to an arbitrary host chosen by the attacker — without the user doing anything unusual, and without any TLS/hostname allowlist check on Desktop's side prior to prompting.

### Impact Explanation
This corrupts the invariant that a GitHub-branded sign-in/account-binding flow in Desktop only ever targets a host the user explicitly configured. Instead, an attacker who controls a git remote or a network proxy in the request path can:
- Spoof "GitHub-ness" of an arbitrary origin via a header they fully control, causing Desktop to bind a new "Enterprise" sign-in attempt to that origin.
- Present the user with an official-looking, Desktop-native sign-in dialog for what the user might reasonably believe is a real GitHub host, harvesting whatever credentials/PAT/OAuth flow the user completes against the attacker's endpoint.

This falls under "unauthorized OAuth or account binding" in the valid impact set, and could lead to credential/token exfiltration if the user completes the resulting sign-in.

### Likelihood Explanation
The trigger path (an outgoing Git network request to a URL the attacker controls, or one intercepted by a proxy) is exactly the "attacker controls…a git remote/proxy response" scenario called out as in-scope. It requires no privileged access, no pre-existing malware, and no unnatural user steps — only that the user perform an ordinary Git operation (fetch/pull/push/clone) against a repository whose remote resolves to (or is proxied through) infrastructure the attacker controls. The only mitigating factor is that the flow still requires the user to actively complete the sign-in dialog for credentials to actually leak, so likelihood is moderate rather than automatic, and I was not able to fully verify (tool budget exhausted) whether the subsequent Enterprise sign-in flow performs any additional server identity/certificate validation before accepting credentials — that remains unverified and would need to be checked in `sign-in-store.ts` and the Enterprise authentication implementation.

### Recommendation
Do not trust the `WWW-Authenticate` realm string as a standalone signal of GitHub identity. At minimum:
- Require corroboration (e.g., only honor the `wwwauth[]` "happy path" for hosts already associated with a known/previously-verified GitHub Enterprise endpoint, or require the `x-github-request-id` verification path already used in `isGitHubHost()`).
- Before invoking `promptForGitHubSignIn`/`beginEnterpriseSignIn` for a host discovered solely via header content, perform the same authoritative check used elsewhere (`isGitHubHost()`/`getEndpointVersion()`), rather than short-circuiting on attacker-suppliable header text.

### Proof of Concept
1. Stand up an HTTPS server (or MITM proxy) at `https://evil.example.com` that responds to any Git HTTP request with `401 Unauthorized` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, add/clone a repository whose remote is `https://evil.example.com/foo/bar.git` (no account currently configured for this host).
3. Trigger a fetch/push. Git captures the header and forwards it (as `wwwauth[0]=...`) to Desktop's credential helper.
4. `getEndpointKind()` returns `'enterprise'` due to the `realm="GitHub"` match; since no account exists for `evil.example.com`, `getCredential()` calls `ui.promptForGitHubSignIn('https://evil.example.com')`.
5. Desktop shows its native "Sign in" dialog bound to `https://evil.example.com`, as if it were a legitimate GitHub Enterprise instance, even though Desktop has performed no independent verification of that host's authenticity. [4](#0-3)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
```
