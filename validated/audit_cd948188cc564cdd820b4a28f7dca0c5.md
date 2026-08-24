### Title
Attacker-crafted "github."-labeled hostnames bypass GitHub-host verification, triggering the Enterprise sign-in flow against a phishing endpoint - ([File: app/src/lib/api.ts])

### Summary
`Claim.claim()` unconditionally trusted `supportsInterface()` for `ERC20DAO` — a capability check that isn't actually validated and just fails/misbehaves. The Desktop analog is `isGitHubHost()` / `getEndpointKind()`, which use a naive substring/regex heuristic (`/(^|\.)(github)\./`) to *decide trust* in a remote host as "GitHub", short-circuiting the actual network verification (`x-github-request-id` check) that would otherwise prove the host is genuine GitHub infrastructure.

### Finding Description
When Git's credential helper needs credentials for an HTTPS remote (triggered during clone, fetch, push, or **recursive submodule update**, e.g. `updateSubmodulesAfterOperation` in [1](#0-0) ), Desktop's trampoline handler calls `getEndpointKind()` to classify the remote host [2](#0-1) .

Inside `isGitHubHost()`, before ever making a network request to verify the `x-github-request-id` header, the code takes a shortcut: any hostname that textually matches `/(^|\.)(github)\./` is unconditionally classified as a GitHub host: [3](#0-2) 

This is a pure string heuristic, not an identity check — an attacker fully controls DNS/hostnames they own, so registering something like `github.attacker-domain.com` or `evil.github.example.net` trivially satisfies the regex and is never subjected to the actual HEAD-request verification that follows (`x-github-request-id` check) at [4](#0-3) .

Once `getEndpointKind()` returns `'enterprise'` for such a host and no existing account matches, `getCredential()` calls `ui.promptForGitHubSignIn(endpoint)`: [5](#0-4) 

`promptForGitHubSignIn` then automatically begins the **GitHub Enterprise sign-in flow** and points it at the attacker-controlled origin without any user-typed URL: [6](#0-5) 

Normally, GHE sign-in requires the user to manually type a URL into Desktop's "Enterprise" login screen — the user is the trust anchor. Here, the trust decision is made silently by Desktop itself based on a naive hostname pattern, so a hostile git server disguised as "github.something" can cause Desktop to open its native-looking Enterprise OAuth/sign-in UI against that attacker server, without the user ever having chosen to add that host as trusted.

### Impact Explanation
This is a broken invariant: "a git remote host is authenticated as GitHub" should require the cryptographic/network verification (`x-github-request-id`) that `isGitHubHost()` performs later in the same function, not a hostname-substring guess. Because the substring check returns early, the real verification is bypassed entirely for any attacker-chosen hostname containing "github.". The result is that Desktop's own UI initiates an OAuth/PAT sign-in flow against a server the attacker controls, risking token/credential exfiltration or binding the user's GitHub identity/session against attacker infrastructure (unauthorized OAuth flow). This matches the valid-impact category "credential/token exfiltration" and "unauthorized OAuth or account binding," and it is reachable purely from cloning/fetching a repository whose remotes/submodules use such a hostname — no local access, malware, or leaked credentials required.

### Likelihood Explanation
Likelihood is **High**: an attacker only needs to register any domain/subdomain containing `github.` (e.g. `github.mycompany-mirror.io`) and get a victim to add it as a remote or, more stealthily, as a submodule URL inside an otherwise innocuous repository that the victim clones. Desktop's recursive submodule update (`git submodule update --init --recursive`) will automatically invoke the credential helper against that URL without further user action, exercising the vulnerable code path.

### Recommendation
Remove the loose regex shortcut (`/(^|\.)(github)\./`) from `isGitHubHost()` in `app/src/lib/api.ts`, and require every unrecognized host to go through the actual verification request (`x-github-request-id` HEAD check) before being classified as an "enterprise"/GitHub host. At minimum, gate the heuristic behind an explicit user-configured allowlist (e.g., accounts the user has already added) rather than trusting hostname substrings, so that only verified or user-established endpoints can trigger the Enterprise sign-in prompt automatically.

### Proof of Concept
1. Attacker creates a public repository and adds a submodule (or a second remote) pointing at `https://github.malicious-mirror.com/evil/evil.git`, a server they control that does not send `x-github-request-id`.
2. Victim clones the repository in GitHub Desktop; Desktop runs `git submodule update --init --recursive` via `updateSubmodulesAfterOperation` [7](#0-6) .
3. Git needs credentials for the submodule URL and invokes Desktop's credential trampoline; `getEndpointKind()` calls `isGitHubHost()`.
4. Because `github.malicious-mirror.com` matches `/(^|\.)(github)\./`, `isGitHubHost` returns `true` immediately without performing the network verification [3](#0-2) , so `getEndpointKind()` returns `'enterprise'`.
5. `getCredential()` finds no matching account and calls `ui.promptForGitHubSignIn(endpoint)`, which opens Desktop's native Enterprise sign-in dialog with `origin` set to the attacker's server [8](#0-7) .
6. The victim, trusting Desktop's own UI, proceeds through what looks like a legitimate GHE OAuth/token sign-in, sending credentials/tokens to the attacker-controlled endpoint.

### Citations

**File:** app/src/lib/git/submodule.ts (L45-54)
```typescript
  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
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

**File:** app/src/lib/api.ts (L2450-2454)
```typescript

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }
```

**File:** app/src/lib/api.ts (L2467-2483)
```typescript
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
