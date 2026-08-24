## Title
Attacker-controlled `WWW-Authenticate` header can spoof "GitHub Enterprise" classification and trigger a real sign-in/OAuth flow bound to an untrusted remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The TRSRY bug is a case of a single approval value being consumed by two functions with different security semantics (`withdrawReserves` vs `getLoan`), so possessing "loan" approval silently grants the stronger, unaudited "withdraw" capability. The Desktop analog is `getEndpointKind()` in the git-credential trampoline handler: it uses attacker-controlled data (the `WWW-Authenticate` HTTP header echoed by whatever remote Git server the user is fetching/cloning from) to classify a completely arbitrary endpoint as `'enterprise'`, and that classification is then used to trigger the full GitHub sign-in (OAuth/device flow) UI bound to the attacker's origin — a materially more sensitive operation than the credential lookup the classifier was meant to gate.

### Finding Description
`getEndpointKind()` decides how a remote endpoint should be treated for credential purposes: [1](#0-0) 

```
const getEndpointKind = async (cred: Credential, store: Store) => {
  ...
  if (isGist(endpoint)) return 'generic'
  if (isDotCom(endpoint)) return 'github.com'
  if (isGHE(endpoint)) return 'ghe.com'

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper...
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (...) return 'generic'
    }
  }
  ...
}
```

The `wwwauth[]` entries come directly from the HTTP `WWW-Authenticate` response header that Git captures from the remote server on a 401 response and forwards verbatim to the credential helper [2](#0-1) . Any Git server the user clones/fetches/pushes to fully controls this string — it is not signed, not validated against the actual endpoint, and requires no prior trust relationship.

This "kind" is then used in `getCredential()` to decide whether to launch the real GitHub sign-in flow: [3](#0-2) 

```
const endpointKind = await getEndpointKind(cred, store)
...
if (endpointKind !== 'generic' && !accounts.some(a => a.endpoint === apiEndpoint)) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
}
```

`promptForGitHubSignIn(endpoint)` treats any non-`github.com` endpoint as "Enterprise" and starts the enterprise sign-in flow bound to that arbitrary `endpoint` origin: [4](#0-3) 

```
const { hostname, origin } = new URL(endpoint)
if (hostname === 'github.com') {
  this.dispatcher.beginDotComSignIn(cb)
} else {
  this.dispatcher.beginEnterpriseSignIn(cb)
  await this.dispatcher.setSignInEndpoint(origin)
}
this.dispatcher.showPopup({ type: PopupType.SignIn, isCredentialHelperSignIn: true, credentialHelperUrl: endpoint })
```

The broken invariant is exactly analogous to TRSRY: `getEndpointKind()`'s `'enterprise'` classification is meant to represent "we have verified/expect this endpoint to be a real GitHub Enterprise Server," but it is derived from unauthenticated, attacker-supplied header content (`realm="GitHub"`) rather than any actual server verification (`isDotCom`/`isGHE` are the only pre-checked, trustworthy conditions; the header-based branch is a heuristic fallback for arbitrary hosts). That single, spoofable signal is then reused to gate a materially different and more sensitive action — initiating a real GitHub sign-in / OAuth session bound to the attacker's origin — the same way TRSRY's `withdrawApproval` mapping was reused to gate both an audited "loan" and an unaudited fund transfer.

### Impact Explanation
A malicious or compromised Git host (e.g., a self-hosted server the user clones from, or a MITM proxy on an insecure `http://` remote) can return a 401 response with `WWW-Authenticate: Basic realm="GitHub"` for any arbitrary domain that is neither `github.com` nor `*.ghe.com`. Desktop's credential helper will then treat that domain as `'enterprise'` and — if the user has no account already registered for that exact endpoint — pop the GitHub sign-in dialog, bind the sign-in flow's endpoint/origin to the attacker's domain via `setSignInEndpoint(origin)`, and proceed with the enterprise sign-in flow. This can be used to social-engineer/legitimize a phishing-style prompt inside Desktop's own trusted "GitHub sign in" UI pointed at an attacker's server, and results in `setSignInEndpoint` and downstream API calls being made to an attacker-controlled origin as part of what looks like a normal, first-party sign-in flow. This falls under "unauthorized OAuth or account binding" triggered purely by an attacker-controlled git remote's HTTP response, with no local access, no existing credentials, and no unnatural user steps beyond adding/fetching from the attacker's repository (a normal Desktop workflow).

### Likelihood Explanation
This requires only that the user add or fetch from a Git remote the attacker controls (or a MITM'd insecure HTTP remote) — a standard, low-friction scenario for GitHub Desktop given users routinely clone third-party/enterprise repositories. The classification code path is reached automatically as part of Git's normal credential-fill negotiation (401 + `WWW-Authenticate`), requiring no special user action beyond a fetch/push that triggers authentication.

### Recommendation
- Do not classify arbitrary endpoints as `'enterprise'` based solely on a `WWW-Authenticate` realm string; require it in conjunction with actual server verification (e.g., successful `x-github-enterprise-version` header from an authenticated GET/HEAD against the endpoint by Desktop itself, not something forwarded by Git from an unauthenticated 401).
- Separate "eligible for GitHub-branded credential handling" from "trusted origin for sign-in flow" the way TRSRY's fix separated `loanApproval` from `withdrawApproval` — i.e., maintain distinct, non-conflatable trust states for "we found a stored account" vs. "we should prompt the user to sign in to a brand-new enterprise endpoint."
- Before invoking `promptForGitHubSignIn`, surface the resolved endpoint/origin to the user explicitly and require confirmation, rather than silently deriving 'enterprise' from a header value the remote server fully controls.

### Proof of Concept
1. Attacker sets up a Git-over-HTTP server (or a MITM proxy for `http://` remotes) at `https://evil.example.com/repo.git`.
2. Victim adds this URL as a remote/clones it in GitHub Desktop and performs a fetch/push that requires authentication.
3. Git sends a request; the attacker's server responds `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper via the trampoline.
5. `getEndpointKind()` returns `'enterprise'` for `evil.example.com` (`app/src/lib/trampoline/trampoline-credential-helper.ts:159-161`).
6. Since the victim has no account registered for `https://evil.example.com`, `getCredential()` calls `ui.promptForGitHubSignIn('https://evil.example.com')` (`trampoline-credential-helper.ts:118`).
7. `promptForGitHubSignIn` calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint('https://evil.example.com')`, launching Desktop's real sign-in dialog bound to the attacker's origin (`trampoline-ui-helper.ts:88-99`).

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-134)
```typescript
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-178)
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
