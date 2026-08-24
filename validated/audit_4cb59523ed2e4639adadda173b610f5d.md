This confirms the analog: `promptForGitHubSignIn(endpoint)` calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` using the attacker's own endpoint, then shows the native `SignIn` popup [1](#0-0) . This is the concrete, exploitable consequence of the misclassification.

### Title
Spoofable GitHub-host detection in credential-helper trampoline triggers unauthorized GitHub sign-in / OAuth flow for attacker-controlled remotes - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's git-credential trampoline decides whether a remote host is a "GitHub" endpoint (`getEndpointKind`) using two attacker-controllable signals: (1) a `WWW-Authenticate` header value forwarded by git itself, and (2) a best-effort HTTP probe (`isGitHubHost`) that trusts a self-reported `x-github-request-id` response header. Just like the Curve reentrancy guard that assumed "a call succeeding" proves the guard engaged (when a Vyper `__default__` fallback trivially satisfies that assumption), Desktop's guard assumes "a header claiming to be GitHub" proves the host is GitHub — a signal any malicious HTTP server can forge with zero cost.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` classifies the target of a credential request as `'github.com' | 'ghe.com' | 'enterprise' | 'generic'`: [2](#0-1) 

The two weak checks are:
1. **`WWW-Authenticate` realm sniffing (lines 157-165)**: git forwards any `wwwauth[...]` header it received from the remote server directly into the credential helper's stdin. If the value contains `realm="GitHub"`, the code immediately classifies the host as `'enterprise'` — no further verification is performed.
2. **`isGitHubHost` (line 178, defined in `app/src/lib/api.ts` lines 2435-2491)**: makes a `HEAD /meta` request and treats `response.headers.has('x-github-request-id')` as proof the host is genuine GitHub: [3](#0-2) 

Both signals are HTTP response headers fully controlled by whatever server the attacker points the git remote at. Nothing about TLS certificates, DNS, or GitHub's actual infrastructure is verified — the "proof" is just an HTTP header, analogous to the Curve issue where "the call didn't revert" was treated as proof of reentrancy-lock engagement even though a bare `__default__` fallback satisfies that condition trivially.

The broken invariant: **`endpointKind === 'enterprise'` is supposed to mean "this is a real GitHub Enterprise host"**, but it can be forced true by any server the attacker controls that git talks to during a fetch/clone/push, with no relation to the actual endpoint being trustworthy.

### Impact Explanation
When `getCredential` sees `endpointKind !== 'generic'` and no existing account matches that endpoint, it calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

`promptForGitHubSignIn` then drives the dispatcher to begin a real Enterprise **sign-in flow bound to the attacker's URL**, using Desktop's own trusted `SignIn` popup chrome: [1](#0-0) 

This is triggered purely by performing an ordinary `git fetch`/`clone`/`push` against a repository whose remote points at an attacker-controlled HTTP server — no unnatural user action, no existing malware, no admin rights. The result is an unauthorized invocation of GitHub Desktop's OAuth/account-binding flow (`dispatcher.beginEnterpriseSignIn` → `setSignInEndpoint(origin)`) pointed at attacker infrastructure, which the "Valid Impact" criteria explicitly lists as in-scope ("unauthorized OAuth or account binding"). It also has a secondary effect: because `storeCredential`/`eraseCredential` bail out whenever `endpointKind !== 'generic'`, an attacker who can force the `'enterprise'`/`'github.com'` classification can also suppress normal credential store/erase side effects for that host.

### Likelihood Explanation
High feasibility: crafting an HTTP response with `WWW-Authenticate: Basic realm="GitHub"` or a `x-github-request-id` response header requires no special privilege — any git-over-HTTP server the attacker operates (or a MITM/malicious proxy on an already-added remote) can add it. The victim only needs to run a normal git operation (clone/fetch/push) inside Desktop against that remote, something Desktop actively encourages via its clone/add-repository UI.

### Recommendation
Do not rely on self-reported headers (`WWW-Authenticate` realm or `x-github-request-id`) as sole proof of GitHub identity. At minimum:
- Require these signals to only *upgrade* classification when combined with a hostname-based allowlist or a previously-established, uncompromised account relationship, rather than allowing a bare header match to authorize triggering the sign-in/OAuth flow for an unknown endpoint.
- Before invoking `promptForGitHubSignIn`, surface the untrusted endpoint's real origin clearly and require explicit user confirmation distinguishing it from genuine GitHub-owned domains.
- Consider removing/hardening the `wwwauth[...]` realm shortcut in `getEndpointKind`, since it is attacker-controlled input passed straight from git without validation.

### Proof of Concept
1. Attacker stands up a plain HTTP git server (e.g., a `git http-backend` CGI or a custom responder) at `http://malicious.example/repo.git`.
2. On any authentication challenge, the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"` (or, without a challenge, serves a `/meta` HEAD response including `x-github-request-id: 1`).
3. Victim adds this URL as a remote / clones it in GitHub Desktop and performs a fetch.
4. Git invokes Desktop's credential helper (`createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind`), which classifies the endpoint as `'enterprise'` purely from the forged header [5](#0-4) .
5. Since no account exists for that endpoint, `ui.promptForGitHubSignIn(malicious-endpoint)` fires, causing Desktop's own `SignIn` UI to begin an Enterprise OAuth/sign-in flow scoped to the attacker's origin [6](#0-5) .

### Citations

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

**File:** app/src/lib/api.ts (L2472-2483)
```typescript
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
