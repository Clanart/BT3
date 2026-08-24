### Title
`getEndpointKind()` trusts an attacker-controlled `WWW-Authenticate` realm to trigger GitHub Enterprise sign-in flow - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Git credential trampoline decides how to treat a remote host (`github.com`, `ghe.com`/`enterprise`, or `generic`) based partly on the `WWW-Authenticate` header the *remote server itself* returns during authentication. If that header contains `realm="GitHub"`, Desktop classifies the host as `enterprise` without ever verifying it against the real GitHub API. This is directly analogous to the audited `claimVestEarlyWithPenalty` bug class: a value that should only be trusted after being derived from a verified/complete computation (here, a verified GitHub host) is instead accepted from an untrusted, attacker-influenced short-circuit path ("happy path"), producing an incorrect state transition (treating a non-GitHub host as GitHub Enterprise) with security-relevant consequences.

### Finding Description
`getEndpointKind()` in [1](#0-0)  is invoked whenever Git contacts a remote and needs credentials (`git credential fill`, triggered from `getCredential()` in the same file, lines 94-135). For any remote that is not `github.com` or a recognized GHE domain, it falls back to inspecting `wwwauth[...]` entries copied from the credential protocol input: [2](#0-1) 

These `wwwauth[]` values originate from the `WWW-Authenticate` HTTP header sent by the remote Git server (or an HTTP(S) proxy sitting in front of it) during the authentication handshake — i.e., fully attacker-controlled content when the "remote" is a malicious/compromised server or a malicious proxy. If the header contains `realm="GitHub"`, the function returns `'enterprise'` immediately, **skipping** the actual verification step `isGitHubHost(endpoint)` (an API-based check) that is used for every other undetermined host at line 178. There is no requirement that the endpoint's TLS certificate, hostname, or actual API response corroborate the claim — the string match on the header is sufficient.

`getCredential()` then uses this classification to decide the user-facing behavior: for any endpoint classified as non-`generic` (`github.com`/`ghe.com`/`enterprise`) that isn't already a known account, it calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn()` then drives the real Enterprise sign-in dialog and OAuth/token flow against the attacker-supplied `endpoint` (the malicious host), because for anything other than `github.com` it calls `beginEnterpriseSignIn` and sets `setSignInEndpoint(origin)` using that endpoint verbatim: [4](#0-3) 

This means a user who merely clones/fetches from (or is proxied through) a malicious HTTPS remote can be shown Desktop's legitimate-looking "Sign in to GitHub Enterprise" dialog, pre-populated to authenticate against the attacker's server — without ever manually choosing to "Add Enterprise account." Any username/password or OAuth callback the user completes in that flow is delivered to the attacker-controlled endpoint.

### Impact Explanation
This is credential/token exfiltration and unauthorized-account-binding-adjacent: the classification bug causes GitHub Desktop to autonomously initiate its trusted "sign in to GitHub Enterprise" UX against a host that was never verified to actually be a GitHub Enterprise server — the only "evidence" is a header value the attacker's own server chose to send. A user who completes the resulting sign-in believing it's a legitimate GHE prompt hands their credentials/token to the attacker's endpoint. It also creates the possibility of establishing a persisted `Account` bound to a spoofed endpoint (`setSignInEndpoint(origin)` + `beginEnterpriseSignIn`), corrupting Desktop's account/credential trust model — mirroring how the vesting contract computation used an untrusted/incorrect basis for a security-relevant calculation.

### Likelihood Explanation
Likelihood is limited primarily by the fact that a `credential fill` request is only issued by Git when the server responds with an HTTP 401 requiring authentication, which is easy for an attacker who controls the destination remote (or a malicious HTTPS proxy/MITM position in front of a non-GitHub remote configured with `http.proxy`/`insteadOf`) to arrange by simply serving `WWW-Authenticate: Basic realm="GitHub"`. No local access, no prior malware, and no unusual user steps are needed beyond the normal action of fetching/pushing to a remote the user has added — which is squarely within the "attacker controls a git remote/proxy response" allowed impact category.

### Recommendation
Never let the `WWW-Authenticate` realm alone elevate an unknown host to `'enterprise'`. Use it only as a weak hint to decide whether to *perform* the authoritative `isGitHubHost(endpoint)` API check (which already exists as the correct fallback), rather than as a substitute short-circuit that bypasses that check. Only classify a host as `enterprise` after a verified `isGitHubHost()` result, and always route it into `getEndpointKind`'s existing verified branch rather than returning directly on header content.

### Proof of Concept
1. Stand up a malicious HTTPS server (or an HTTP proxy in front of a benign non-GitHub Git remote) that responds to unauthenticated Git requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, add/clone this remote as a repository (a normal, unprivileged action; no existing account for this endpoint).
3. Trigger a fetch/push so Git invokes the credential helper; observe `getEndpointKind()` return `'enterprise'` purely from the header, causing `getCredential()` to call `ui.promptForGitHubSignIn(endpoint)`.
4. Desktop shows the "Sign in to GitHub Enterprise" dialog scoped to the attacker's endpoint; anything entered/authorized there is delivered to the attacker's server, not a real GitHub Enterprise instance.

*(Note: exact behavior of the sign-in dialog's network calls (`beginEnterpriseSignIn`, `setSignInEndpoint`) was inspected only at the call-site level shown above; deeper verification of downstream OAuth/token-exchange code was not performed due to the iteration limit, so confirmation of the full request path to the attacker endpoint would need further review of `Dispatcher.beginEnterpriseSignIn`/`SignInStore`.)*

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
