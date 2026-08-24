### Title
Spoofable `WWW-Authenticate` / GitHub-detection heuristics let a malicious HTTPS remote trigger GitHub Enterprise sign-in and credential storage for an untrusted host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The report's root cause is a broken interface-conformance assumption: code trusts that a remote object "looks like" a known-good implementation (Uniswap V3 pool) based on superficial signals, without verifying the real interface, so an attacker-controlled remote object drives the caller into an unintended path. The Desktop analog is `getEndpointKind()` in the git-credential trampoline handler, which decides whether an HTTPS remote is a genuine GitHub/GHE host using signals that are fully attacker-controlled (HTTP response headers), then acts on that classification by launching the GitHub Enterprise sign-in/OAuth flow and persisting credentials against that host.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` classifies a git remote endpoint as `'github.com'`, `'ghe.com'`, `'enterprise'`, or `'generic'` in order to decide how the git credential helper (invoked from git's `credential.helper` protocol during fetch/push/clone) should behave.

Two of the classification signals are values that git forwards verbatim from the HTTP response of whatever server the remote points to, with no cryptographic or identity verification: [1](#0-0) 

1. `wwwauth[...]` credential fields — git captures the `WWW-Authenticate` header from a 401 response and forwards it to the helper. If the value contains `realm="GitHub"`, Desktop treats the host as `'enterprise'`.
2. `isGitHubHost()` (in `app/src/lib/api.ts`) performs a `HEAD /meta` request and treats the presence of an `x-github-request-id` response header as proof the host is genuine GitHub/GHE: [2](#0-1) 

Both signals are trivially forgeable by any HTTP server the attacker controls (a malicious git remote, a corporate/attacker-run proxy, or a MITM on a non-pinned connection): a server can simply return `WWW-Authenticate: Basic realm="GitHub"` on a 401, or add an `x-github-request-id` header to its responses. There is no TLS certificate pinning, no signature check, and no server-side confirmation that ties these headers to an actual github.com/GHE deployment — the OracleLibrary-style "same shape means same trust" assumption from the original report.

When `getEndpointKind` returns `'enterprise'`, `getCredential()`'s flow prompts the user to sign in via `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

which, for a non-`github.com` hostname, calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` against the attacker's origin: [4](#0-3) 

If the user completes the sign-in dialog (which they naturally would, believing Desktop is asking them to authenticate to a legitimate GHE instance because Desktop itself has flagged it as GitHub-branded), Desktop performs an enterprise sign-in/OAuth exchange with the attacker-controlled `origin`, and the resulting account/token becomes bound to that endpoint and is later fed back as GitHub credentials (`credWithAccount`) for that host on subsequent git operations.

### Impact Explanation
This matches the "unauthorized OAuth or account binding" and "credential/token exfiltration" categories: a malicious git remote can get Desktop to (a) misidentify it as a trusted GitHub/GHE endpoint purely via forgeable headers, (b) initiate an authentication/OAuth flow scoped to the attacker's origin, and (c) subsequently transmit the resulting GitHub token/credentials to that attacker-controlled host whenever git needs credentials for it. This is triggered simply by adding such a remote (or being redirected/proxied to such a host) and performing a normal fetch/push/clone — no local access, no prior malware, and no leaked credentials are required; the attacker only needs to control the HTTP responses of the git remote endpoint.

### Likelihood Explanation
Likelihood is moderate: the attacker needs the victim to add or use a remote pointing at their server (a plausible scenario for supply-chain style attacks, malicious forks with rewritten remotes, or MITM/rogue proxy on an HTTPS connection without pinning), and the victim must respond to the resulting sign-in prompt. However, unlike an obvious phishing page, the prompt is framed by Desktop's own UI as a legitimate "GitHub Enterprise sign-in," which increases the chance a user completes it, and no unnatural steps are required beyond git's ordinary credential-helper invocation during fetch/push.

### Recommendation
Do not classify a host as a trusted GitHub/Enterprise endpoint based on values the remote server can freely choose (`WWW-Authenticate` realm text, `x-github-request-id` header). Instead:
- Require an explicit, out-of-band trust decision (e.g., only endpoints the user has already registered as an Account, or ones added through the verified "Add Enterprise server" flow) before invoking `beginEnterpriseSignIn`/`setSignInEndpoint`.
- If heuristic detection must remain for UX convenience, treat it strictly as a hint for pre-filling the sign-in dialog, never as an implicit "this is GitHub" trust decision that auto-launches OAuth against an arbitrary origin.
- Consider validating GHE identity via a robust mechanism (e.g., checking `/api/v3/meta` for internally-consistent GitHub Enterprise metadata plus a warning that a human must confirm, rather than a single spoofable header).

### Proof of Concept
1. Attacker sets up an HTTPS git server (e.g., self-hosted `git http-backend` or any server that can respond to git's smart HTTP protocol) and gets a victim to add it as a remote, e.g. `https://evil.example.com/attacker/repo.git`, or intercepts/redirects an existing HTTPS remote to this server.
2. The attacker's server responds to unauthenticated git requests with `401` plus header `WWW-Authenticate: Basic realm="GitHub"` (or, absent that, responds to Desktop's discovery `HEAD /meta` request with header `x-github-request-id: <anything>`).
3. Victim runs `git fetch`/`git push`/clone through GitHub Desktop against this remote. Git invokes the Desktop credential-helper trampoline (`createCredentialHelperTrampolineHandler`) with the captured `wwwauth[...]` header in the credential map.
4. `getEndpointKind` at `app/src/lib/trampoline/trampoline-credential-helper.ts:153-178` matches `realm="GitHub"` (or falls through to `isGitHubHost`) and returns `'enterprise'`.
5. `getCredential` at lines 107-125 calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint = https://evil.example.com`.
6. `promptForGitHubSignIn` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-99`) calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint('https://evil.example.com')`, presenting the user with what looks like a legitimate "Sign in to GitHub Enterprise" dialog for the attacker's domain.
7. If the user completes sign-in, the resulting account/token is bound to `https://evil.example.com` and will be sent to that server as Basic-auth credentials on subsequent git operations against it (via `credWithAccount`/`getGitHubCredential`), giving the attacker the user's Enterprise/GitHub session token.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-178)
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
```

**File:** app/src/lib/api.ts (L2465-2484)
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
  } catch (e) {
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
