Found a concrete analog: `getEndpointKind` in the credential-helper trusts a self-reported, attacker-controllable `WWW-Authenticate` header to classify a remote host as an "enterprise" GitHub endpoint — a decision made without ever verifying (via API round-trip) that the host is actually GitHub, mirroring the escrow bug's pattern of trusting an unverified, attacker-supplied signal to short-circuit the "real verification" path.

### Title
Credential-helper trusts spoofable `WWW-Authenticate: realm="GitHub"` header to classify hosts, bypassing the real GitHub-host verification check - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind` decides whether a git remote is treated as `'enterprise'` (a GitHub/GHE host) or `'generic'` by first checking attacker-controlled `wwwauth[]` credential fields captured from the remote server's HTTP response, before ever falling back to the actual verification function `isGitHubHost`, which performs a real network probe (`/meta` HEAD request, checking for `x-github-request-id`) [1](#0-0) . Because the `wwwauth[]` value is just an HTTP response header echoed back by whatever server the remote URL points to, a malicious or MITM'd remote can set `WWW-Authenticate: realm="GitHub"` and force Desktop's credential flow down the "enterprise" path without any actual confirmation that the host is GitHub.

### Finding Description
`getCredential` first tries `getGitHubCredential`, and if that fails it calls `getEndpointKind` to classify the endpoint [2](#0-1) . Inside `getEndpointKind`, after checking `isGist`/`isDotCom`/`isGHE` (all based on hostname string matching, which a non-GitHub attacker host will fail), the function inspects the `cred` map for keys starting with `wwwauth[` and, if any value contains `realm="GitHub"`, immediately returns `'enterprise'` — skipping the network-based `isGitHubHost` check entirely [3](#0-2) . The comment even states the intent is to avoid "having to resort to making a request ourselves" — i.e., this is explicitly a trust shortcut around the real verification path [4](#0-3) .

This is the same broken-invariant pattern as the escrow bug: a value that should only be set after real verification (an actual git-fetch trip confirming the host is genuinely GitHub, via `isGitHubHost`'s `/meta` probe) is instead accepted on the strength of unauthenticated, self-reported data from the very party being verified (the remote server's own HTTP header). `isGitHubHost` is the "did the transfer actually happen" check; the `wwwauth[]` shortcut is the "msil.value == 0 but still trusted" bypass.

Downstream impact of `endpointKind === 'enterprise'`: in `getCredential`, if `endpointKind !== 'generic'` and there's no existing account for that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which invokes `dispatcher.beginEnterpriseSignIn(cb)` and `setSignInEndpoint(origin)` [5](#0-4) [6](#0-5) . This forces the GitHub Enterprise OAuth/sign-in flow to be shown for an arbitrary attacker-controlled `origin`, rather than the generic username/password credential prompt that would normally be used for unknown hosts.

### Impact Explanation
An attacker who controls a git remote/proxy (e.g., a malicious clone URL, a compromised or MITM'd HTTPS git server) can respond to Git's authentication challenge with a crafted `WWW-Authenticate: realm="GitHub"` header. When the user's clone/fetch/push triggers Desktop's credential helper, this forces the app to treat the attacker's host as a legitimate GitHub Enterprise instance and launch the enterprise sign-in flow bound to the attacker's endpoint, instead of the safer generic-credential prompt. This misclassification is a lower-severity confused-deputy/UI-trust issue rather than direct token exfiltration, since `findGitHubTrampolineAccount` only returns credentials for accounts whose stored `endpoint` origin already matches the remote's origin — so an attacker cannot yet steal an existing GitHub.com/GHE token through this path alone. The primary confirmed impact is that Desktop's own trust classification of "is this a GitHub host" (used to decide sign-in flow and whether to treat credentials as GitHub-managed vs. generic) can be spoofed via a value that never received genuine verification.

### Likelihood Explanation
High: any repository or fork the user clones, adds as a remote, or is redirected to via a malicious "Clone in Desktop" link controls the HTTP responses Git receives, and can include the `WWW-Authenticate` header on a 401 response with no special privileges. This requires no local access, admin rights, or social engineering beyond the user performing normal git operations against a repository the attacker controls.

### Recommendation
Remove or de-prioritize the `wwwauth[]` short-circuit in `getEndpointKind`, or treat it only as a hint that still requires confirmation via `isGitHubHost`'s network-based `/meta` probe before classifying an endpoint as `'enterprise'`. At minimum, don't let an untrusted header alone route the user into a GitHub Enterprise sign-in flow bound to an attacker-supplied origin.

### Proof of Concept
1. Host a git-over-HTTPS server (or MITM proxy) that, on any authentication challenge for a URL like `https://attacker.example.com/repo.git`, replies with `401` and header `WWW-Authenticate: realm="GitHub"`.
2. In GitHub Desktop, clone or fetch from `https://attacker.example.com/repo.git`.
3. Git's credential protocol forwards the `wwwauth[]=realm="GitHub"` line to Desktop's credential helper (`trampoline-credential-helper.ts`).
4. `getEndpointKind` matches the `realm="GitHub"` substring and returns `'enterprise'` without ever calling `isGitHubHost` to verify against the real host.
5. Since no account exists for `attacker.example.com`, Desktop calls `promptForGitHubSignIn('https://attacker.example.com')`, launching the GitHub Enterprise sign-in dialog against the attacker's origin rather than the expected generic username/password prompt.

**Note on limitations:** I could not fully trace whether any newer code path (outside what the index returned) further trusts `endpointKind === 'enterprise'` to attach an existing token automatically to the attacker's origin, which would elevate this to direct credential exfiltration; some file contents may be excluded from the index due to size limits. A Devin session with full repository access could verify whether `findGitHubTrampolineAccount`'s origin-matching is the only gate, or whether other consumers of `getEndpointKind`/`isGitHubHost` create a path where an attacker-controlled host receives a real GitHub token.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-178)
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
