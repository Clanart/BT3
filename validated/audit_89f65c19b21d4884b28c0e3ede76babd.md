### Title
Attacker-controlled `WWW-Authenticate` header spoofs GitHub host classification and bypasses the real host-verification check - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
Notional's bug came from trusting an unauthenticated, third-party-controllable signal (Aura's pre/post reward balance) as the sole basis for a security/financial decision, with no independent verification. The same broken-invariant shape exists in Desktop's git credential-helper trampoline: it trusts an attacker-controlled `WWW-Authenticate` header value returned by *any* git remote to decide whether that remote is a genuine GitHub/GitHub Enterprise host, and this check is evaluated *before* the app's own network-based verification (`isGitHubHost`), effectively bypassing the real guard.

### Finding Description
When Desktop's git credential helper is asked to supply credentials for a host it doesn't already recognize as `github.com`/`*.ghe.com`, it inspects the `wwwauth[]` credential attributes that git forwards from the remote's 401 response, and unconditionally trusts a `realm="GitHub"` string to classify the endpoint as `'enterprise'`: [1](#0-0) 

This check runs *before* the codebase's own legitimate verification function, `isGitHubHost()`, which performs an actual network probe (checking for the `x-github-request-id` header on a real request) to determine if a host is genuinely GitHub-flavored: [2](#0-1) 

Because the `wwwauth[]` value is fully attacker-controlled (any HTTPS git server the user fetches/clones from can respond to an unauthenticated request with `WWW-Authenticate: Basic realm="GitHub"`), an attacker who merely runs a git server (or a proxy sitting in front of one) can force `getEndpointKind` to return `'enterprise'` for their own host without ever passing the real `isGitHubHost` check. This matches the report's attacker primitive category "a git remote/proxy response."

The corrupted value is the `endpointKind` classification returned by `getEndpointKind`. Downstream, `getCredential` uses this classification to decide the trust path: [3](#0-2) 

Because `endpointKind !== 'generic'` and no existing account matches the endpoint, Desktop will surface `ui.promptForGitHubSignIn(endpoint)` — Desktop's native "Sign in to GitHub Enterprise" flow — for a host that was never actually verified to be GitHub. This is functionally the same broken invariant as the Sherlock finding: a value used for a security-relevant decision (there: fee amount; here: "is this GitHub") is derived from data an unprivileged third party fully controls, with the one existing safeguard (`isGitHubHost`'s real network check / the reward-pool's real balance accounting) short-circuited rather than enforced.

### Impact Explanation
By spoofing the header, an attacker-controlled git remote can:
- Force Desktop's native, trusted "Sign in to GitHub Enterprise" UI to trigger for an arbitrary attacker host during an ordinary fetch/clone/push, without the user ever having manually entered or vetted that host as an Enterprise endpoint.
- Cause the user, believing they are going through Desktop's legitimate GitHub Enterprise OAuth/PAT sign-in flow, to complete authentication against the attacker's server; Desktop will then store that endpoint as a bound "enterprise" `Account`, i.e., unauthorized account binding to an unverified host.
- Bypass the one mitigation (`isGitHubHost`) explicitly designed in the code to make this determination safely via a real network round trip, since the `wwwauth[]` branch returns early and the network check is only reached as a last resort.

This falls squarely in the "unauthorized OAuth or account binding" impact bucket in scope: an unprivileged attacker (anyone who can stand up or proxy a git server the victim fetches from) achieves a security-relevant, unintended trust decision inside Desktop with no local access, no admin rights, and no prior compromise.

### Likelihood Explanation
Exploitation only requires the victim to run a normal git operation (clone/fetch/push) against a URL controlled or proxied by the attacker — no unusual user action, no local access, no leaked credentials, and no social-engineering beyond what any malicious-repository scenario already requires. The `wwwauth[]` forwarding is a built-in git behavior that Desktop opted to trust as a "happy path" specifically "without having to resort to making a request" — a deliberate trust shortcut around the codebase's own stronger verification, which is exactly the same "shortcut trusted over authoritative check" pattern flagged as Medium severity in the Notional report.

### Recommendation
Do not treat the `wwwauth[]` `realm="GitHub"` value as authoritative for classifying an unknown host as `'enterprise'`. At minimum, always corroborate it with the existing `isGitHubHost()` network verification (or another verifiable signal, e.g., pinned domain suffix) before offering the GitHub Enterprise sign-in flow, rather than allowing the header-derived branch to short-circuit that check entirely.

### Proof of Concept
1. Attacker stands up an HTTPS git server (or an MITM/compromised proxy in front of an existing remote) at `https://evil.example.com/repo.git`.
2. Victim, using GitHub Desktop, adds this URL as a remote and performs `git fetch`/clone through Desktop.
3. Git issues an unauthenticated request; the attacker's server responds with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as a `wwwauth[0]=Basic realm="GitHub"` attribute to Desktop's credential helper trampoline.
5. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) matches `realm="GitHub"` and returns `'enterprise'` — no call to `isGitHubHost()` ever occurs for this host.
6. `getCredential` (lines 107-125) sees `endpointKind !== 'generic'` and no matching stored account, and calls `ui.promptForGitHubSignIn(evil.example.com)`, presenting Desktop's native GitHub Enterprise sign-in dialog for the attacker's host. [4](#0-3)

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
