## Title
`isGitHubHost()`'s naive hostname regex lets an attacker-registered domain be trusted as a GitHub Enterprise host, bypassing the actual verification and triggering credential prompts to the attacker's server - (File: `app/src/lib/api.ts`)

### Summary
`isGitHubHost()` is Desktop's authoritative check for deciding whether an arbitrary git remote host is a genuine GitHub instance. The *real* verification is an authenticated network probe that checks for the `x-github-request-id` response header [1](#0-0) , which only GitHub's own edge/servers can produce. However, before that probe runs, the function short-circuits to `true` for any hostname that merely contains the label `github.` — a purely cosmetic, attacker-controlled string: [2](#0-1) 

This is structurally the same flaw as the Palmera report: a security-critical identity check (`isSafe()` verifying a "real" Safe wallet vs. `isGitHubHost()` verifying a "real" GitHub host) is supposed to rely on an authoritative signal (a Safe-specific `getThreshold()` call vs. GitHub's `x-github-request-id` header), but a weaker, easily-satisfied heuristic (`threshold != 0` vs. `hostname` substring/regex match) is evaluated first and lets any attacker-controlled entity pass as trusted.

### Finding Description
`isGitHubHost(url)` is called from the git credential-fill trampoline whenever Git needs credentials for a remote and no cached GitHub account exists for that exact endpoint: [3](#0-2) 

Inside `isGitHubHost`, before the actual network-based `/meta` probe is attempted, several fast-path heuristics run. The dangerous one is:

```js
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```

Any domain the attacker registers where one DNS label is exactly `github` (e.g. `github.attacker.com`, `sub.github.attacker.com`) satisfies this regex and causes the function to return `true` immediately — the code never reaches the `fetch(metaUrl, ...)` check that verifies the `x-github-request-id` header, which is the only signal actually controlled by GitHub. `isKnownThirdPartyHost` is checked first but only protects a small fixed allow-list of unrelated third parties [4](#0-3) ; it does nothing to stop an attacker from choosing a domain of their own.

Once `isGitHubHost` returns `true`, `getEndpointKind()` classifies the remote as `'enterprise'` [5](#0-4) . Back in `getCredential()`, because the kind is not `'generic'` and no account is registered for that endpoint yet, Desktop automatically pops the GitHub Enterprise sign-in UI targeted at the attacker's host: [6](#0-5) 

The victim only has to add/clone a remote whose hostname the attacker controls (e.g. `https://github.attacker.com/foo/bar.git`) — a completely ordinary "clone this repo" workflow, no local access or pre-existing malware needed.

### Impact Explanation
Because the trust decision is made on cosmetic hostname content rather than the authoritative header probe, Desktop will treat an attacker-chosen domain as a legitimate GitHub Enterprise instance. This:
- Surfaces a "Sign in to GitHub Enterprise" prompt pointed at the attacker's server during what looks like a normal fetch/push, priming credential/OAuth phishing against a victim who has no reason to suspect the domain isn't a real GHE deployment.
- Silently reclassifies the endpoint as `'enterprise'` instead of `'generic'`, which skips the intended generic-git-credential storage/lookup path entirely (`storeCredential`/`eraseCredential` bail out for non-generic kinds) [7](#0-6) , silently corrupting Desktop's normal credential-handling behavior for that remote.
- Undermines the entire purpose of the `/meta` + `x-github-request-id` check, which exists specifically to make this determination based on something the remote host cannot forge without actually being fronted by/mimicking GitHub's infrastructure.

### Likelihood Explanation
Registering a domain containing the label `github` (e.g. via a subdomain like `github.<attacker-domain>.com`) is trivial and entirely within attacker control — no privileged access, no compromise of GitHub infrastructure, and no unnatural user steps beyond adding/cloning a remote that points at the attacker's URL, which is a normal Desktop workflow (`open-repository-from-url` / manual clone).

### Recommendation
Remove the hostname-substring fast path (`/(^|\.)(github)\./`) from `isGitHubHost()`, or at minimum never let it short-circuit *positively* without also passing the `/meta` header verification. If a fast path is desired for known/self-hosted patterns, it should only be used to skip the network probe for hosts that are already registered `Account` endpoints (already handled earlier via `findGitHubTrampolineAccount`), not for arbitrary unauthenticated URLs.

### Proof of Concept
1. Attacker registers `github.attacker.com` and stands up a plain HTTPS git server there (no need to mimic GitHub headers).
2. Victim clones/adds a remote `https://github.attacker.com/foo/bar.git` in GitHub Desktop (e.g., via a link, README instruction, or `x-github-client://openRepo/...` action).
3. On the first `git fetch`/`push`, Git invokes the Desktop credential helper trampoline, which calls `getEndpointKind` → `isGitHubHost('https://github.attacker.com')`.
4. `/(^|\.)(github)\./.test('github.attacker.com')` evaluates `true`, so the function returns `true` without ever hitting the `/meta` request that checks `x-github-request-id`.
5. `getEndpointKind` returns `'enterprise'`; since no matching `Account` exists, `getCredential()` invokes `ui.promptForGitHubSignIn('https://github.attacker.com')`, presenting the victim with a GitHub Enterprise sign-in flow scoped to the attacker's domain. [8](#0-7) [9](#0-8)

### Citations

**File:** app/src/lib/api.ts (L2407-2427)
```typescript
const knownThirdPartyHosts = new Set([
  'dev.azure.com',
  'gitlab.com',
  'bitbucket.org',
  'amazonaws.com',
  'visualstudio.com',
])

const isKnownThirdPartyHost = (hostname: string) => {
  if (knownThirdPartyHosts.has(hostname)) {
    return true
  }

  for (const knownHost of knownThirdPartyHosts) {
    if (hostname.endsWith(`.${knownHost}`)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/api.ts (L2429-2491)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

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
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
```

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L181-213)
```typescript
/** Implementation of the 'store' git credential helper command */
async function storeCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? storeExternalCredential(cred, token)
    : setGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username')),
        forceUnwrap(`credential missing password`, cred.get('password'))
      )
}

const storeExternalCredential = (cred: Credential, token: string) => {
  const path = getTrampolineEnvironmentPath(token)
  return approveCredential(cred, path, getGcmEnv(token))
}

/** Implementation of the 'erase' git credential helper command */
async function eraseCredential(cred: Credential, store: Store, token: string) {
  if ((await getEndpointKind(cred, store)) !== 'generic') {
    return
  }

  return useExternalCredentialHelper()
    ? eraseExternalCredential(cred, token)
    : deleteGenericCredential(
        urlWithoutCredentials(getCredentialUrl(cred)),
        forceUnwrap(`credential missing username`, cred.get('username'))
      )
}
```
