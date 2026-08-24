Based on my analysis, I found a genuine analog in the codebase. GitHub Desktop's endpoint derivation for a repository does not force HTTPS, and account matching is done by hostname only (ignoring protocol) — this reproduces the exact "hostname vs. origin" confusion described in the report and can lead to sending an authenticated account token over plaintext HTTP.

### Title
Authenticated API endpoint derived from remote URL preserves attacker-controlled protocol, enabling HTTP token exfiltration - (File: app/src/lib/api.ts)

### Summary
`getEndpointForRepository` builds the API endpoint used for authenticated requests directly from the git remote URL's own `protocol`, instead of forcing `https:`. Combined with `matchGitHubRepository`/`findAccountForRemoteURL`, which associate a GitHub/GHE account to a repository by comparing hostname only (not full origin), a repository whose remote uses `http://` for a hostname the user already has an Enterprise account for will have its account token attached to requests sent over plaintext HTTP.

### Finding Description
`getEndpointForRepository` parses the repository's remote URL and echoes back its protocol verbatim: [1](#0-0) 

If `parsed.hostname !== 'github.com'`, the endpoint is built as `` `${parsed.protocol}//${parsed.hostname}/api` `` — if the remote is `http://github.mycompany.com/owner/repo`, the derived endpoint is `http://github.mycompany.com/api`, not HTTPS.

Separately, account-to-repository association is done by comparing hostnames only, ignoring protocol/origin: [2](#0-1) [3](#0-2) 

Both `matchGitHubRepository` and `findAccountForRemoteURL` use `parsedRemote.hostname === hostname` / `parsedURL.hostname === parsedEndpoint.hostname` checks — they never compare protocol. This is precisely the invariant broken in the external report: hostname is treated as equivalent to origin, when in fact `http://github.mycompany.com` and `https://github.mycompany.com` are different origins with different trust levels.

Notably, this codebase already fixed the analogous issue correctly in one place — `findGitHubTrampolineAccount`, used by the git credential helper, compares full `origin` (protocol+host+port), not just hostname: [4](#0-3) 

That guard, however, only protects git's own credential-helper flow (`git credential fill`). It does not protect the `api.ts` endpoint-derivation path, because `getEndpointForRepository` is a separate code path used to compute which endpoint the app's authenticated `fetch`-based API client should target for PR/CI/status data associated with a repository, and it is reached whenever the app has matched an account to a repository via the hostname-only comparisons above.

### Impact Explanation
If a user adds/clones a repository (or a repository is fetched/opened) whose remote points to `http://<enterprise-hostname>/...` (attacker can arrange this via a malicious fork, a tampered `.git/config`, an `openrepo` deep link parsed by `parseAppURL`, or a MITM-rewritten redirect), and the user already has a legitimate GHE account for that hostname over HTTPS, GitHub Desktop's hostname-only matching will associate that account with the repository. Subsequent authenticated API calls derived via `getEndpointForRepository` will be sent to the `http://` endpoint, causing the account's OAuth/PAT token (added via `Authorization` headers by the `Octokit`/fetch API client) to be transmitted in plaintext, exfiltratable by any on-path attacker — the exact MITM/HTTP-downgrade credential-theft scenario described in the report.

### Likelihood Explanation
Requires: (1) the victim already has an authenticated GHE account, and (2) a remote/deep-link/redirect that resolves to `http://` for that same hostname. Both conditions are plausible without local access, admin rights, or social engineering beyond opening/cloning an attacker-supplied repository or clicking a crafted `x-github-client://openRepo/http://...` deep link, which `parseAppURL` explicitly accepts without protocol validation.

### Recommendation
- In `getEndpointForRepository`, always force `https:` for the derived endpoint protocol (mirroring the pattern already used by `validateURL` in `app/src/ui/lib/enterprise-validate-url.ts` and by `findGitHubTrampolineAccount`'s origin comparison).
- In `matchGitHubRepository` and `findAccountForRemoteURL`, compare full origin (protocol + hostname + port), not hostname alone, when deciding whether to attach an authenticated account to a remote URL.

### Proof of Concept
1. User has an authenticated GHE account for `https://github.mycompany.com`.
2. User clones/opens a repository whose `origin` remote is `http://github.mycompany.com/owner/repo` (e.g., via a crafted deep link `x-github-client://openRepo/http://github.mycompany.com/owner/repo`, accepted by `parseAppURL` in `app/src/lib/parse-app-url.ts`).
3. `matchGitHubRepository`/`findAccountForRemoteURL` match the account by hostname, ignoring the `http:` scheme.
4. `getEndpointForRepository('http://github.mycompany.com/owner/repo')` returns `http://github.mycompany.com/api`.
5. The app issues authenticated API requests (PR status, checks, etc.) to that HTTP endpoint, leaking the account token to any network intermediary. [5](#0-4)

### Citations

**File:** app/src/lib/api.ts (L2267-2280)
```typescript
/**
 * Map a repository's URL to the endpoint associated with it. For example:
 *
 * https://github.com/desktop/desktop -> https://api.github.com
 * http://github.mycompany.com/my-team/my-project -> http://github.mycompany.com/api
 */
export function getEndpointForRepository(url: string): string {
  const parsed = URL.parse(url)
  if (parsed.hostname === 'github.com') {
    return getDotComAPIEndpoint()
  } else {
    return `${parsed.protocol}//${parsed.hostname}/api`
  }
}
```

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
}
```

**File:** app/src/lib/find-account.ts (L38-69)
```typescript
export async function findAccountForRemoteURL(
  urlOrRepositoryAlias: string,
  accounts: ReadonlyArray<Account>,
  canAccessRepository: RepositoryLookupFunc = canAccessRepositoryUsingAPI
): Promise<Account | null> {
  const allAccounts = [...accounts, Account.anonymous()]

  // We have a couple of strategies to try to figure out what account we
  // should use to authenticate the URL:
  //
  //  1. Try to parse a remote out of the URL.
  //    1. If that works, try to find an account for that host.
  //    2. If we don't find an account move on to our next strategy.
  //  2. Try to parse an owner/name.
  //    1. If that works, find the first account that can access it.
  //  3. And if all that fails then throw our hands in the air because we
  //     truly don't care.
  const parsedURL = parseRemote(urlOrRepositoryAlias)
  if (parsedURL) {
    const account =
      allAccounts.find(a => {
        const htmlURL = getHTMLURL(a.endpoint)
        const parsedEndpoint = URL.parse(htmlURL)
        return parsedURL.hostname === parsedEndpoint.hostname
      }) || null

    // If we find an account whose hostname matches the URL to be cloned, it's
    // always gonna be our best bet for success. We're not gonna do better.
    if (account) {
      return account
    }
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

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```
