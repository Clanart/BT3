### Title
Overly-permissive GitHub-host heuristic lets an attacker-controlled domain be classified as "GitHub Enterprise," triggering credential prompts/OAuth against a phishing host - ([File: app/src/lib/api.ts])

### Summary
The external report's root issue is a validation threshold set far too loosely (`MAX_PRICE_DEVIATION_UPPER_BOUND = 500`, i.e. ±5%), letting attacker-influenced input (oracle price) pass a check that should have rejected it, with a "stable" (trusted) asset being treated as valid when it clearly is not. The Desktop analog is the `isGitHubHost` / `getEndpointKind` heuristic used by the git-credential trampoline: a regex-based "looks like GitHub" check is broad enough that a fully attacker-controlled remote hostname can be classified as a genuine GitHub Enterprise host, causing Desktop to treat it as trusted and prompt the user to sign in / hand over GitHub credentials against that attacker domain.

### Finding Description
`isGitHubHost` decides whether an arbitrary endpoint "belongs to a GitHub host": [1](#0-0) 

The critical gate is:
```
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```
This only requires the label `github` to appear as a dot-delimited segment somewhere in the hostname (at the start, or preceded by a `.`). It does not require `github` to be the *registrable domain* — any attacker who registers a domain such as `github.attacker-controlled.io` or points a subdomain like `sub.github.attacker-controlled.io` at a remote satisfies the regex and is classified as a trusted GitHub host, bypassing the later network probe (`/meta` request) entirely (that probe path is only reached when none of the earlier fast-path checks match) [2](#0-1) .

This heuristic feeds directly into the git-credential trampoline's decision of whether to treat a remote as a GitHub endpoint: [3](#0-2) 

In `getEndpointKind`, once earlier checks (`isGist`, `isDotCom`, `isGHE`, WWW-Authenticate header sniffing, existing-account lookup, protocol check) fall through, the function falls back to `isGitHubHost(endpoint)` and returns `'enterprise'` if it matches. Back in `getCredential`, an `endpointKind !== 'generic'` result with no matching stored account causes Desktop to invoke `ui.promptForGitHubSignIn(endpoint)` [4](#0-3) , i.e. it surfaces a "Sign in to GitHub Enterprise" flow scoped to the attacker's endpoint rather than treating the remote as an unknown/generic git host that would just prompt for a plain username/password (or use a generic credential helper) without implying any GitHub trust relationship.

Existing guards do not stop this path because:
- The `knownThirdPartyHosts` denylist (gitlab.com, bitbucket.org, etc.) only rejects a fixed list of known competitors, not arbitrary attacker domains [5](#0-4) .
- The `github`/`bitbucket`/`gitlab` substring regexes run *before* any network verification, so a domain merely containing the label `github.` short-circuits straight to `true` without ever hitting the `/meta` HEAD request that would otherwise validate the `x-github-request-id` response header.
- `findGitHubTrampolineAccount` only matches by `origin`, so it won't silently exfiltrate an *existing* GitHub.com credential to the fake host [6](#0-5) , but the classification still steers the user into a GitHub-Enterprise-styled sign-in/OAuth flow bound to the attacker's endpoint, which is the exploitable surface.

### Impact Explanation
An attacker who controls a git remote (e.g., via a cloned/fetched repository whose `.git/config` or submodule points at a crafted host, or a link that adds such a remote) can get Desktop to misclassify their host as GitHub Enterprise. This causes Desktop's credential trampoline to invoke the GitHub Enterprise sign-in UI against the attacker's endpoint, which can lead to unauthorized OAuth/account binding to an attacker-controlled "enterprise" endpoint and social-engineering-free credential exposure risk purely from the hostname string, not from any real verification of the target server's identity. This aligns with the "unauthorized OAuth or account binding" and "credential/token exfiltration" impact categories called out as valid.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to add/fetch a remote whose hostname contains a `github.`-prefixed label (trivially achievable by registering any domain with that label, no privileged access or malware needed), and then have git actually attempt authentication against it (e.g., a private/protected repo on that host) so the credential-helper trampoline is invoked. No local access, admin rights, or prior compromise is required — only attacker control of a remote URL/domain, which matches the report's valid-impact criteria.

### Recommendation
Tighten `isGitHubHost`'s fast-path heuristics so a hostname is only fast-tracked as a GitHub host when `github` (or `ghe`) is the actual eTLD+1 registrable domain (e.g., using a public-suffix-list-aware parse), not merely a dot-delimited label anywhere in the hostname. Any hostname that doesn't match a known GitHub.com/GHE domain exactly should always fall through to the network-verified `/meta` probe (checking `x-github-request-id`) before being treated as trusted, and unverifiable/ambiguous hosts should default to `'generic'` (undefined/false) rather than `'enterprise'`, mirroring the report's recommendation to fail closed on ambiguous/wide-tolerance situations rather than keep "minting" trust for something that doesn't actually pass verification.

### Proof of Concept
1. Register (or control) a domain such as `github.attacker.io`.
2. Configure it to serve git-over-HTTPS for a repository (any generic git server works; it does not need to emulate GitHub's API/meta endpoint).
3. Have the victim add a remote pointing to `https://github.attacker.io/foo/bar.git` (e.g., via a cloned repo's `.git/config`, a submodule URL, or a deep link that adds a remote) and perform a fetch/pull that requires authentication.
4. Git invokes the Desktop credential-helper trampoline; `getEndpointKind` reaches the fallback branch and calls `isGitHubHost('https://github.attacker.io/...')`.
5. The regex `/(^|\.)(github)\./.test('github.attacker.io')` returns `true` (label `github` at the start followed by `.`), so `getEndpointKind` returns `'enterprise'` without ever issuing the `/meta` verification request.
6. `getCredential` finds no existing account for that origin and calls `ui.promptForGitHubSignIn('https://github.attacker.io/...')`, presenting the victim with a GitHub-Enterprise-branded sign-in flow scoped to the attacker's domain. [7](#0-6) [8](#0-7)

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

**File:** app/src/lib/api.ts (L2429-2463)
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
```

**File:** app/src/lib/api.ts (L2465-2491)
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
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
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
