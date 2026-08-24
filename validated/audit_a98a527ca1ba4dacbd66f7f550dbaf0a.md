### Title
Malicious git remote/proxy spoofs `WWW-Authenticate` header to force an Enterprise sign‑in flow bound to an attacker‑controlled endpoint - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
Analogous to the Frax bug, where an unverified, externally‑supplied value (`setDollarBalances`, populated by an out‑of‑band script) is trusted for a security‑critical calculation (`globalCollateralValue`) without independent verification, GitHub Desktop's credential trampoline trusts an **attacker‑controlled** signal — the `WWW-Authenticate` header returned by the remote git server during a `fetch`/`clone`/`push` — to classify an arbitrary host as GitHub Enterprise, before any cryptographic or authoritative verification is performed.

### Finding Description
`getEndpointKind()` in [1](#0-0)  determines how Desktop treats a git host that is contacting the credential helper. After ruling out known first‑party hosts (`isDotCom`, `isGHE`), it inspects `wwwauth[...]` credential fields: [2](#0-1) 

These `wwwauth[]` values originate from the `WWW-Authenticate` HTTP header sent by the git server itself during the authentication handshake — data that is entirely under the control of whoever operates the remote (or a MITM proxy sitting on the path), matching the report's "attacker controls...a git remote/proxy response" primitive. If that header contains `realm="GitHub"`, the function immediately returns `'enterprise'` **without making any network call to verify the claim** (the actual verification via `isGitHubHost(endpoint)` only happens in the fallback branch when no `wwwauth[]` header is present).

This classification feeds directly into `getCredential()`: [3](#0-2) 

When `endpointKind !== 'generic'` and no existing account matches the endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` — automatically initiating a GitHub Enterprise sign‑in flow bound to the attacker's endpoint: [4](#0-3) 

`promptForGitHubSignIn` calls `dispatcher.setSignInEndpoint(origin)` with the attacker‑supplied `origin`, so any account created through this flow becomes permanently bound (via `endpoint`) to the attacker's host, per `accountEquals`: [5](#0-4) .

The broken invariant is identical to the Frax report: **a value with security consequences (`endpointKind`, i.e. "is this GitHub?") is set from an unauthenticated, externally supplied signal and consumed by downstream logic (account creation, credential release) without independent verification**, exactly as `collatDollarBalance` was consumed by `globalCollateralValue` without validation of the off-chain script's correctness.

### Impact Explanation
- If the user completes the triggered sign‑in (e.g., enters an Enterprise personal access token, or completes OAuth against a phishing page hosted at the attacker's domain), the resulting `Account` — including its long‑lived token — is bound to the attacker's endpoint (`unauthorized OAuth or account binding` / `credential exfiltration` classes from the Valid Impact list).
- Even short of a full sign‑in, this happens automatically the moment Desktop performs a `fetch`/`clone`/`push` against a remote that returns a crafted header — no unusual user action is required to trigger the misclassification itself, since git authentication challenges are a normal part of cloning/fetching from private-looking remotes.
- Existing guards (`isDotCom`, `isGHE`, hostname allow‑lists) are bypassed entirely because the `wwwauth[]` branch short‑circuits before the only real verification (`isGitHubHost`, an actual network probe) is reached.

### Likelihood Explanation
Moderate‑to‑high: any git server (or a network‑positioned proxy for an unauthenticated HTTP remote) can freely set response headers during the git credential‑challenge handshake; this requires no compromise of the user's machine, no prior credentials, and no admin rights — only that the user add or fetch from an attacker‑controlled/MITM'd remote, which is a normal Desktop workflow (cloning/adding a remote).

### Recommendation
Do not treat the `wwwauth[]` realm string as authoritative for GitHub/Enterprise classification. At minimum, require the authoritative `isGitHubHost()` network check (or equivalent server-side verification, e.g., checking for GitHub-specific response headers on a request Desktop itself controls) before ever returning `'enterprise'`/`'github.com'`, and before invoking `promptForGitHubSignIn`. Additionally, surface the actual endpoint hostname prominently in the sign-in UI so users can detect a mismatch between the remote they intended to use and the endpoint being authenticated.

### Proof of Concept
1. Attacker stands up a git HTTPS remote (or a MITM proxy in front of any HTTP(S) git remote) at `https://evil.example.com/repo.git`.
2. Victim adds/clones this remote in GitHub Desktop.
3. When git requests credentials, the attacker's server responds to the initial unauthenticated request with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as a `wwwauth[...]` field to Desktop's credential helper (`getCredential` → `getEndpointKind`), which returns `'enterprise'` for `evil.example.com` without any real verification (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`).
5. Since no account is currently associated with `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com')` (`trampoline-credential-helper.ts:109-124`), which opens a "Sign in to GitHub Enterprise" dialog and calls `setSignInEndpoint(origin)` with the attacker's origin (`trampoline-ui-helper.ts:87-93`).
6. If the victim completes sign-in (PAT or OAuth), the resulting token is now associated with `evil.example.com` and future git/API operations directed at that account may transmit the token to the attacker-controlled endpoint.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
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

**File:** app/src/models/account.ts (L13-15)
```typescript
export function accountEquals(x: Account, y: Account) {
  return x.endpoint === y.endpoint && x.id === y.id
}
```
