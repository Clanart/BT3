### Title
Malicious Git remote can spoof `WWW-Authenticate: realm="GitHub"` to trick Desktop into sending a real GitHub token to an attacker-controlled host - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind()` in the credential-helper trampoline classifies an unknown remote as a "GitHub" endpoint purely by checking whether Git forwarded a `wwwauth[...]` credential field containing the literal string `realm="GitHub"` — a value that originates from the remote server's own HTTP `WWW-Authenticate` response header and is therefore fully attacker-controlled when the remote is malicious. This mirrors the reported bug class: a security-relevant classification is derived from unauthenticated attacker-suppliable data instead of a verified source, and a "should be untrusted" result (an arbitrary, non-GitHub host) is treated as "trusted" (a GitHub/Enterprise host), analogous to `ecrecover()`'s spoofable failure output being accepted as a valid signer.

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's credential helper trampoline (`createCredentialHelperTrampolineHandler` → `getCredential`) with a credential description populated from the request context, including any `WWW-Authenticate` headers the server returned (`wwwauth[N]=...`). [1](#0-0) 

`getEndpointKind()` treats `v.includes('realm="GitHub"')` as sufficient proof that the remote is a GitHub Enterprise host, before falling back to the network-verified `isGitHubHost()` check: [2](#0-1) 

This `wwwauth[...]` value is not verified in any way — it is simply the string the remote server sent in its `WWW-Authenticate` HTTP header, which any attacker-controlled server (or MITM proxy) can set arbitrarily. Because the `realm="GitHub"` branch is checked *before* the `isGitHubHost()` network probe, a malicious server never has to answer that probe honestly.

Once `getEndpointKind()` returns `'enterprise'`, `getCredential()` checks whether an account already exists for `getAPIEndpoint(endpoint)` (which, for the fake host, resolves to something like `https://api.<evil-host>/`). Since the user has no account for that made-up endpoint, Desktop proceeds to prompt the user to sign in as if this were a legitimate GitHub Enterprise instance: [3](#0-2) 

`promptForGitHubSignIn()` then drives a real sign-in flow (dotcom OAuth if the sign-in target happens to look like github.com, or Enterprise sign-in against the attacker's `origin` otherwise): [4](#0-3) 

If the user completes this sign-in (e.g., because Desktop's UI now presents it as a normal "Sign in to GitHub" flow tied to the current clone/fetch/push operation), the resulting `Account` — which carries a real, valid GitHub API token — is merged into the credential and handed straight back to Git via `credWithAccount`: [5](#0-4) 

Git will then use that `login`/`token` pair as the HTTP Basic auth credentials for the *original malicious remote*, sending the user's real GitHub token to the attacker's server. Existing origin-matching protections (`findGitHubTrampolineAccount`, used for the "already have an account" fast-path) do not apply here, because this path is only reached specifically when no existing account already matches, and it is not that lookup but a fresh interactive sign-in whose resulting token gets attributed to the untrusted host.

### Impact Explanation
This allows an attacker who controls a git remote/proxy (e.g. a malicious `http(s)://` clone URL, a compromised or MITM'd Git server) to exfiltrate the user's real GitHub OAuth/PAT token by faking a `WWW-Authenticate: ... realm="GitHub"` response. The victim, believing they are performing a normal GitHub sign-in prompted by Desktop, hands their token to the attacker's server. This matches the "credential/token exfiltration" and "unauthorized OAuth" categories in the Valid Impact criteria, requires no local/physical access, and needs only that the victim clone/fetch/push against an attacker-supplied remote — an unprivileged, attacker-controlled input.

### Likelihood Explanation
Likelihood is high in a targeted phishing/supply-chain scenario: an attacker just needs to get a victim to add or use a malicious HTTPS remote (e.g., via a shared repo URL, "helpful" mirror, corporate proxy, or compromised infra), and to have Git actually challenge for credentials (trivial — return `401` with the crafted header). No race conditions, no privileged access, and no dependency on any existing account state are required; the flow is specifically reached only when the user has no prior account matching the fake host, which is the common case for a brand-new attacker-controlled remote.

### Recommendation
Do not trust the `wwwauth[...]` `realm=` value as authoritative proof of a GitHub Enterprise host. At minimum:
- Require the network-verified `isGitHubHost()` (or equivalent TLS/API check) to succeed before treating an unknown host as `'enterprise'`, rather than short-circuiting on a spoofable header.
- If the `realm="GitHub"` heuristic is kept as a fast path, still validate it against a real API round-trip (e.g., confirm the endpoint actually exposes `x-github-request-id` /`/meta`) before initiating an interactive sign-in flow tied to that endpoint.
- Clearly warn the user in the sign-in prompt when it was triggered by an *unverified* heuristic for a non-dotcom, non-configured-GHE host, distinguishing it from a normal, previously-trusted Enterprise sign-in.

### Proof of Concept
1. Attacker sets up an HTTPS git server at `https://evil.example.com/repo.git` (or a MITM proxy in front of any HTTP git remote).
2. Victim runs `git clone`/fetch/push against this remote from within GitHub Desktop (e.g., after being sent the URL, or via a compromised submodule/mirror).
3. When Git requests credentials, the attacker's server responds to the auth challenge with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper (`createCredentialHelperTrampolineHandler` → `getCredential`).
5. `getEndpointKind()` matches `realm="GitHub"` and returns `'enterprise'` without ever contacting the real host to verify. [6](#0-5) 
6. Since no account exists for `https://api.evil.example.com/`, Desktop calls `ui.promptForGitHubSignIn('https://evil.example.com/...')`, which opens Desktop's normal-looking sign-in dialog tied to `evil.example.com`. [3](#0-2) 
7. The victim, trusting the prompt (which appears to originate from Desktop mid-clone), completes GitHub.com OAuth sign-in via the real GitHub OAuth flow (unrelated to the malicious remote).
8. The resulting real Account token is merged via `credWithAccount` and returned as the credential Git will use to authenticate to `evil.example.com`, at which point the attacker's server receives the victim's valid GitHub token in the HTTP Basic Authorization header. [5](#0-4)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
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
