### Title
`findAccountForRemoteURL` compares GitHub hostnames without case normalization, breaking account selection on cloned/pasted repo URLs - ([File: app/src/lib/find-account.ts])

### Summary
The BN254/BLS12-381 `Fr` advisory is a case of "two representations of the same logical value being compared without first canonicalizing both sides." `app/src/lib/find-account.ts` has the same class of bug: it compares hostnames extracted from two different parsing paths — one canonicalized (lowercased) and one not — using strict string equality (`===`), instead of a case-insensitive comparison.

### Finding Description
`findAccountForRemoteURL` decides which stored `Account` (and therefore which GitHub token) should be used to authenticate a clone/fetch for a user-supplied URL or `owner/name` alias.

Strategy 1 (line 55-62): [1](#0-0) 
`parsedURL.hostname` comes from `parseRemote()`, which extracts the hostname with a raw regex capture group and does **not** normalize case: [2](#0-1) 
`parsedEndpoint.hostname` comes from `URL.parse(getHTMLURL(a.endpoint))`, which is always lowercase because Node's `url.parse`/`URL` normalize hostnames.

Strategy 2 (line 93-100) has the identical asymmetry: `accountHost` is derived via `URL.parse` (lowercased), while `hostname` comes from `parseRepositoryIdentifier` → `parseRemote` (raw case preserved): [3](#0-2) 

Both comparisons are strict `===`/`!==` on strings, with no `.toLowerCase()` normalization — unlike the sibling module `app/src/lib/repository-matching.ts`, which performs the *same kind* of hostname comparison but correctly wraps it in `caseInsensitiveEquals`/`.toLowerCase()`: [4](#0-3) 

Hostnames are case-insensitive per RFC 3986/DNS, so `HTTPS://GitHub.MyCompany.com/owner/repo.git` and `https://github.mycompany.com/owner/repo.git` are the *same* remote, exactly analogous to `r+1` and `1` being the same field element in the advisory. `find-account.ts` treats them as different because one side is reduced (lowercased) and the other is not.

### Impact Explanation
`findAccountForRemoteURL` is the mechanism used when a user opens/clones a repository from a URL (including via `x-github-client://` deep links and pasted clone URLs) in `app/src/ui/clone-repository/clone-repository.tsx`. If the URL's host casing differs from the account's canonical endpoint host (e.g. an Enterprise remote written with mixed/upper case, which Git and browsers accept without complaint), both matching strategies silently fail to find the correct authenticated account and the function falls through to `Account.anonymous()` or `null`. Practically this means:
- A private Enterprise/GitHub repository is treated as unauthenticated, causing the clone/fetch to fail or to be attempted anonymously, and
- Because the failure is silent (no error, just "no account found"), the user gets confusing behavior with no indication that a matching account existed.

This does not directly leak credentials to an attacker-controlled host (unlike the Fr bug's authorization-bypass framing), but it is the exact same *broken invariant* — case-insensitive identifier compared without canonicalization — reachable purely from an attacker-controlled or externally supplied URL (deep link/clone URL), with no local access or malware required.

### Likelihood Explanation
Attacker/adversarial control here is limited to crafting the hostname casing in a repository URL (e.g. a shared link, README instruction, or `x-github-client://openLocalRepo`/`cloneRepo` deep link). No privileged access is needed to trigger the mismatch; case differences in hostnames occur naturally too (e.g. copy-pasted URLs, enterprise names typed with a different case), making the bug likely to trigger unintentionally as well as intentionally.

### Recommendation
Normalize both sides of the hostname comparisons in `find-account.ts` the same way `repository-matching.ts` does, e.g. wrap both comparisons with `caseInsensitiveEquals` (from `app/src/lib/compare.ts`) or lowercase both `parsedURL.hostname`/`hostname` and `parsedEndpoint.hostname`/`accountHost` before comparing at lines 61 and 97.

### Proof of Concept
```ts
import { findAccountForRemoteURL } from '../../src/lib/find-account'
import { Account } from '../../src/models/account'
import { getEnterpriseAPIURL } from '../../src/lib/api'

const accounts = [
  new Account('joel', getEnterpriseAPIURL('https://github.mycompany.com'), 'deadbeef', [], '', 2, 'My Company', 'free'),
]

// Same host, different case — should match the same account as the lowercase form.
const account = await findAccountForRemoteURL(
  'https://GitHub.MyCompany.com/owner/private-repo.git',
  accounts
)
console.log(account) // null — expected `joel`, matches with lowercase URL succeed
``` [5](#0-4)

### Citations

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

**File:** app/src/lib/find-account.ts (L93-100)
```typescript
    for (const account of sortedAccounts) {
      if (hostname != null) {
        const htmlURL = URL.parse(getHTMLURL(account.endpoint))
        const accountHost = htmlURL.hostname
        if (accountHost !== hostname) {
          continue
        }
      }
```

**File:** app/src/lib/remote-parsing.ts (L27-59)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]

/** Parse the remote information from URL. */
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
```

**File:** app/src/lib/repository-matching.ts (L90-104)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }
```
