### Title
Attacker-controlled `WWW-Authenticate` header spoofs a git remote as "GitHub Enterprise", triggering unwarranted GitHub sign-in / credential binding - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
GitHub Desktop's git credential helper (`getEndpointKind`) classifies an arbitrary git remote host as a GitHub/GitHub Enterprise endpoint by trusting a `WWW-Authenticate` HTTP header value that is captured from the remote server's own response and forwarded, unverified, into the credential-resolution logic. [1](#0-0) 

### Finding Description
`getEndpointKind` is used by the trampoline credential helper to decide whether a git host being authenticated against should be treated as `'github.com'`, `'ghe.com'`, `'enterprise'`, or `'generic'`: [2](#0-1) 

The relevant branch reads:
```
for (const [k, v] of cred.entries()) {
  if (k.startsWith('wwwauth[')) {
    if (v.includes('realm="GitHub"')) {
      return 'enterprise'
    } ...
``` [3](#0-2) 

The comment above this block explains the trust assumption explicitly: "When Git attempts to authenticate with a host it captures any WWW-Authenticate headers and forwards them to the credential helper. We use them as a happy-path to determine if the host is a GitHub host without having to resort to making a request ourselves." [4](#0-3) 

This header originates entirely from the remote git server (or an interposing proxy) responding to an HTTPS Git request — it is not authenticated, signed, or cross-checked against TLS identity in any way at this call site. Any attacker who controls a git remote/proxy that the user clones/fetches/pushes to can return `WWW-Authenticate: Basic realm="GitHub"` and have Desktop classify their arbitrary host as `'enterprise'`.

Once `endpointKind` resolves to `'enterprise'` for a host with no matching stored account, `getCredential` invokes the GitHub-specific sign-in flow instead of the generic username/password credential path: [5](#0-4) 

```
if (
  endpointKind !== 'generic' &&
  !accounts.some(a => a.endpoint === apiEndpoint)
) {
  ...
  const account = await ui.promptForGitHubSignIn(endpoint)
  ...
  return credWithAccount(cred, account)
}
``` [6](#0-5) 

The legitimate "safe" path that actually verifies GitHub identity — `findGitHubTrampolineAccount`, which compares URL origins of already-known accounts — is only consulted first for *existing* stored accounts and does not gate this spoofed classification for hosts without a prior account. [7](#0-6) 

Note also that the non-heuristic fallback (`isGitHubHost`, invoked when no `wwwauth[...]` header is present) is equally spoofable: it treats any HTTP response containing an `x-github-request-id` header as proof of being a GitHub host, which is likewise just an attacker-settable response header with no cryptographic binding. [8](#0-7) 

### Impact Explanation
An attacker who controls a git remote (or a MITM/proxy that a victim's Desktop client talks to) can force GitHub Desktop into believing that host is a GitHub Enterprise server purely by shaping response headers. This causes Desktop to leave its "generic git host" credential path (plain username/password prompt, isolated per-endpoint) and instead invoke the GitHub-branded sign-in/account-binding flow (`ui.promptForGitHubSignIn`) inside the trusted Desktop chrome for an endpoint the attacker fully controls. This is a trust/UI-confusion and account-binding integrity issue: Desktop's classification of "is this GitHub" — which subsequently controls whether OAuth-derived credentials are handed to the host via `credWithAccount` — is decided by unauthenticated, attacker-supplied header content rather than by verified server identity.

### Likelihood Explanation
Any repository the user clones or adds a remote for, or any transparent proxy/MITM position on the network path to a git remote, can trigger this by simply returning a `401` with `WWW-Authenticate: Basic realm="GitHub"` (or omitting it and returning `x-github-request-id` for the `isGitHubHost` fallback) — no special privileges, no prior compromise of the victim host, and no unusual user action beyond adding/using an attacker-supplied git remote, which is squarely within the "attacker controls a git remote/proxy response" threat model.

### Recommendation
Do not classify a remote as a GitHub/GHE host based solely on client-observable, attacker-controlled response headers (`WWW-Authenticate` realm string or `x-github-request-id`). At minimum, require this classification to be corroborated by an already-trusted account origin match (as done in `findGitHubTrampolineAccount`) or by validating against a pinned/allow-listed set of endpoints the user has explicitly configured (e.g., previously added GitHub Enterprise server URLs), rather than trusting arbitrary hosts' self-reported headers to switch into the GitHub sign-in/account-binding code path.

### Proof of Concept
1. Attacker sets up a git-over-HTTPS server (or MITM proxy) at `https://evil.example.com/some/repo.git`.
2. Victim adds this as a remote in GitHub Desktop and performs a fetch/push that requires authentication.
3. The attacker's server responds to the auth challenge with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git captures this header and forwards it via `wwwauth[...]` to Desktop's credential helper.
5. `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` (lines 153-166) returns `'enterprise'` for `evil.example.com`.
6. Because no stored account matches `evil.example.com`, `getCredential` (lines 109-125) invokes `ui.promptForGitHubSignIn(endpoint)`, presenting the user with Desktop's native "Sign in to GitHub Enterprise" UI for what is, in reality, an attacker-controlled host — enabling account-binding/credential-handling behavior intended only for verified GitHub hosts to be triggered by an unverified third party.

Note: I was unable to fully trace `ui.promptForGitHubSignIn` / `getAPIEndpoint` internals (ran out of tool budget) to confirm whether a subsequent real GitHub OAuth token could end up being silently forwarded as Basic-auth credentials to `evil.example.com` on a later request (via `credWithAccount` once `accounts.some(a => a.endpoint === apiEndpoint)` becomes true). That escalation path is plausible from the code read so far but not fully confirmed — a Devin session with full file access would be needed to trace `getAPIEndpoint` and `promptForGitHubSignIn` to verify whether real token exfiltration to the attacker endpoint occurs, versus only a spoofed sign-in prompt.

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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```

**File:** app/src/lib/api.ts (L2465-2483)
```typescript
  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
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
