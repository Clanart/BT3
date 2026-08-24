### Title
Attacker-controlled `WWW-Authenticate` realm header lets a malicious git remote force GitHub Desktop to open an unsolicited Enterprise OAuth sign-in / account-binding flow - (`app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()`, the function that decides how the Desktop credential-helper trampoline should treat a remote host, classifies a host as `'enterprise'` (a trusted GitHub host) purely by inspecting the `WWW-Authenticate` realm string that the *remote git server itself* returns, with no cryptographic or out-of-band verification. Any git remote the user clones/fetches/pushes to — i.e. fully attacker-controlled infrastructure — can set this header and get Desktop to treat it as a legitimate GitHub Enterprise endpoint, which then triggers an automatic Enterprise sign-in/OAuth prompt bound to the attacker's own host.

### Finding Description
`getEndpointKind()` in [1](#0-0)  loops over the credential parameters that Git forwards from the server's HTTP response and, if it finds a `wwwauth[...]` header whose value contains `realm="GitHub"`, immediately returns `'enterprise'` — no additional verification (TLS pinning, known-host list, or actual API probe) is performed for this branch. This classification happens *before* the protocol/host-based checks and before the network probe `isGitHubHost()` is even attempted [2](#0-1) .

That result feeds directly into `getCredential()`: [3](#0-2)  — if the endpoint "kind" isn't `'generic'` and no existing account matches the URL, Desktop calls `ui.promptForGitHubSignIn(endpoint)`.

`promptForGitHubSignIn()` in [4](#0-3)  then unconditionally starts an Enterprise sign-in flow bound to the attacker's own hostname (`dispatcher.beginEnterpriseSignIn(cb)` + `setSignInEndpoint(origin)`), and shows a sign-in popup.

This mirrors the structure of the ODSafeManager bug: a value the caller does not own or control (the safe-owner's permission bit, here Desktop's classification of "is this a GitHub host") is derived from a signal the untrusted party fully controls (the remote server's own response header) rather than from an independently verifiable fact, letting the untrusted party unlock a privileged code path (owner-only `allowSAFE`, here `promptForGitHubSignIn`/account binding).

### Impact Explanation
A malicious or compromised git remote (self-hosted server, spoofed HTTP proxy, or MITM on plain HTTP) can force GitHub Desktop to present a "sign in to GitHub Enterprise" OAuth flow scoped to an arbitrary hostname chosen entirely by the attacker, without the user ever explicitly choosing "Add Enterprise account." Because this occurs mid-clone/fetch, it can be used to normalize/legitimize a phishing sign-in prompt for a look-alike domain, and any resulting account is silently added to Desktop's account store bound to that attacker-controlled endpoint (`setSignInEndpoint`/`beginEnterpriseSignIn`), matching the "unauthorized OAuth or account binding" impact category.

### Likelihood Explanation
Triggering the vulnerable path only requires the user to clone or fetch from/push to a repository hosted on infrastructure the attacker controls (a very common Desktop action — cloning a repo from an untrusted URL), and for the server to answer an authentication challenge with a crafted `WWW-Authenticate: ... realm="GitHub"` header, which any HTTP server operator can trivially do. No local access, admin rights, or prior compromise is required.

### Recommendation
Do not trust the server-supplied `realm` value as sufficient evidence that a host is GitHub/GHE. At minimum, require the `isGitHubHost()` network probe (which checks `x-github-request-id` from a real, redirect-verified endpoint) to succeed before classifying a URL as `'enterprise'`, and never auto-launch a sign-in/account-binding flow for a host that isn't already a known/added GitHub Enterprise endpoint — instead surface a warning that the remote claims to be a GitHub host and let the user explicitly opt into adding it as an Enterprise account through the normal "Add Enterprise Account" UI.

### Proof of Concept
1. Attacker sets up a plain HTTP or HTTPS git server at `https://evil.example`.
2. Victim runs "Clone repository" in GitHub Desktop using `https://evil.example/attacker/repo.git`.
3. Git contacts the server; the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as a `wwwauth[...]` credential parameter to Desktop's credential helper.
5. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`).
6. `getCredential()` finds no matching account for `evil.example` and calls `ui.promptForGitHubSignIn('https://evil.example')`.
7. Desktop opens `PopupType.SignIn` and calls `dispatcher.beginEnterpriseSignIn` + `setSignInEndpoint('https://evil.example')`, initiating an OAuth/account-binding flow the victim never explicitly requested, bound to the attacker's domain.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-165)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
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
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
  }
```
