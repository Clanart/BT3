### Title
Attacker-controlled `WWW-Authenticate` realm header spoofs GitHub Enterprise identity and triggers a phishing-grade sign-in prompt for an arbitrary host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` classifies a git-credential request as belonging to a GitHub Enterprise host purely by matching the string `realm="GitHub"` inside a `WWW-Authenticate` header that the remote HTTP server itself supplies during the git credential handshake: [1](#0-0) 

This is the same broken-invariant pattern as the Canto oracle bug: trust is derived from a non-unique, attacker-suppliable string (a token `symbol()` there, an HTTP header value here) instead of a stable, verifiable identifier (an immutable address there, the endpoint's real GitHub identity/certificate here).

### Finding Description
When Desktop's git credential trampoline is invoked (`get` command) for any remote URL, `getEndpointKind` first checks fast-path hostname rules (`isDotCom`, `isGHE`), then falls back to inspecting the raw `wwwauth[...]` entries forwarded from git, which originate from the server's HTTP response headers: [2](#0-1) 

Any HTTPS server can return `WWW-Authenticate: Basic realm="GitHub"` on a 401 response and this code will classify the endpoint as `'enterprise'` with zero cryptographic or DNS-based verification. This classification then feeds directly into `getCredential`: [3](#0-2) 

Because `apiEndpoint` is derived from the attacker's own hostname, `accounts.some(a => a.endpoint === apiEndpoint)` is false for any host the user hasn't already added, so Desktop calls `ui.promptForGitHubSignIn(endpoint)` with the attacker's `endpoint` string. That function funnels the flow into the real "GitHub Enterprise sign in" UI (`beginEnterpriseSignIn` / `setSignInEndpoint`) and shows a `SignIn` popup that is visually indistinguishable from a legitimate GHE sign-in, but is bound to `credentialHelperUrl: endpoint` — the attacker's domain: [4](#0-3) 

The attacker primitive: any git remote, redirect target, HTTP proxy in the fetch/push/clone/submodule path, or a URL an attacker gets the user to add as a remote can serve this header during a git operation and cause Desktop to spontaneously pop a "Sign in to GitHub" dialog scoped to the attacker's host.

### Impact Explanation
Existing guards (`isDotCom`, `isGHE`, exact `accounts.some(a => a.endpoint === apiEndpoint)` match) only prevent *reuse* of an already-stored token for a mismatched host — they do nothing to stop the initial classification from being spoofed. The corrupted value is the return of `getEndpointKind`, which downstream code treats as ground truth about "is this a legitimate GitHub host" without ever validating the header against a known GitHub fingerprint (e.g., `x-github-request-id`, which `isGitHubHost` *does* check via an actual network round trip, but that path is only reached when no `wwwauth[...]` header is present — the header short-circuits and skips that verification entirely). Since the resulting sign-in flow is presented as a normal GHE authentication (`beginEnterpriseSignIn`), a user who completes it for the attacker's domain (e.g., typing GitHub Enterprise credentials, or completing an OAuth device/browser flow that the attacker's server can proxy/relay) ends up authenticating to/against an attacker-controlled endpoint, and Desktop will then use those credentials as the git username/password for that endpoint going forward — credential exfiltration triggered purely by a spoofable string comparison, with no local access, admin rights, or pre-existing malware required.

### Likelihood Explanation
The trigger is a single HTTP response header (`WWW-Authenticate: Basic realm="GitHub"`) returned by any server the app's embedded git process talks to during ordinary operations (clone, fetch, push, submodule update) — this is fully reachable by a malicious/compromised remote, a malicious HTTP proxy, or a redirect chain, without any unnatural user interaction beyond doing a normal git operation against a repository the attacker controls or has tampered with in transit.

### Recommendation
Do not classify an endpoint as `'enterprise'`/trusted GitHub based on the `WWW-Authenticate` realm string alone. Always corroborate with the network-verifiable check already implemented in `isGitHubHost` (the `x-github-request-id` response header check) before granting `'enterprise'` classification, or require it to only short-circuit for hosts the user has already explicitly added as a GitHub Enterprise account (i.e., rely on `findGitHubTrampolineAccount` match first, and never use the header alone to justify launching a sign-in prompt for a brand-new, previously-unknown host).

### Proof of Concept
1. Attacker stands up an HTTPS git server (or a MITM/redirect in front of a legitimate-looking URL) that responds to unauthenticated git-http requests with `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim runs any git operation in Desktop against a remote pointing at that server (e.g., adds it as a remote, or the repo has a submodule/`insteadOf` rewrite pointing there).
3. Git's http backend invokes the credential trampoline; `getCredential` → `getEndpointKind` sees the `wwwauth[...]` header matching `realm="GitHub"` and returns `'enterprise'` [5](#0-4) .
4. Since no account exists for that endpoint, `ui.promptForGitHubSignIn(endpoint)` is invoked, opening the "Sign in to GitHub Enterprise" popup bound to the attacker's URL [6](#0-5) .
5. The user, believing this is a legitimate corporate GHE sign-in prompted by Desktop, enters credentials/token, which are then used/stored for the attacker-controlled endpoint.

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
