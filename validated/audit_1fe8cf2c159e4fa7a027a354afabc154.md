## Analysis

The report's broken invariant is: **an authorization/whitelist check is performed against one identity (`msg.sender`), while the credited/acted-upon entity is a different, attacker-choosable identity (`_user`)** — letting an attacker satisfy the gate with a "clean" identity while the effects land on a different one.

The closest concrete analog I found in GitHub Desktop is in the credential trampoline that services `git credential get` requests during clone/fetch/push operations.

### Title
Credential trampoline releases a signed-in account's token based on a per-request `host`/`url` value supplied by the remote, not bound to the repository's actual configured origin - (File: `app/src/lib/trampoline/find-account.ts`)

### Summary
`findGitHubTrampolineAccount` decides which signed-in `Account` (and therefore which OAuth token) to hand back to `git` purely by comparing the *origin* of whatever `host`/`url` fields are present in the current credential-helper request against the endpoints of the user's signed-in accounts. [1](#0-0) 
That request-scoped URL is produced fresh for every credential request via `getCredentialUrl`, built from whatever `url`/`protocol`/`host`/`path` values `git` passes at that moment — not from the repository's originally configured remote. [2](#0-1) 
`getGitHubCredential`/`getEndpointKind` then trust this per-request origin match to decide whether to silently return `credWithAccount(cred, account)`, i.e. the account's `login` and `token`. [3](#0-2) [4](#0-3) 

### Finding Description
This is structurally the same class of bug as the RealityCards report: the code checks one thing (the *origin embedded in the current git credential-fill request*, which is influenced by the remote/proxy that git is currently talking to) and, on that basis, hands over credentials that are meant to be scoped to a *different* thing (the account the user actually intended to authenticate against for the repository they opened in Desktop). There is no verification that the request's `host`/`url` corresponds to the remote that was configured for the `Repository` the trampoline session was started for — `getCredentialUrl` simply parses whatever fields are present in the current libgit2/git-credential exchange (`cred.get('url')` or `protocol`/`host`/`path`) and that value alone drives the account lookup. [2](#0-1) 

Desktop is aware of this general class of risk for `fetch()`-based API calls and built `same-origin-filter.ts` specifically to strip auth headers on cross-origin redirects for the app's own HTTP client. [5](#0-4) 
No equivalent origin-continuity protection exists for the git credential trampoline path: if the remote host that `git` is currently negotiating with at the moment it asks for credentials differs from the repository's actual configured GitHub remote (e.g., because the connection was redirected, proxied, or the server otherwise reports a different `host`/`url` in the credential-helper exchange), `findGitHubTrampolineAccount` will still happily match that reported origin against the user's signed-in accounts and release the token for whichever account has a matching endpoint — regardless of which repository/remote the user believes they are authenticating for. [1](#0-0) 

### Impact Explanation
If exploitable, the practical effect mirrors the report's impact category ("credential/token exfiltration" and "silent corruption of what the user commits/pushes"): a cloned/fetched repository whose remote (or an intermediary the connection passes through) can influence the `host`/`url` presented back through git's credential protocol could cause Desktop to hand its OAuth token for a legitimate account to a connection that is not actually the user's intended, trusted GitHub/GHE endpoint, since the only check performed is an origin string comparison against attacker/server-influenced data rather than the repository's bound remote.

### Likelihood Explanation
Likelihood is **uncertain and not fully confirmed** from static review alone. Modern `git` itself restricts following HTTP redirects to different hosts by default (mitigating some of the classic "leak credentials to redirect target" scenarios), so the primary backstop here is `git` core's own redirect protection rather than anything in Desktop's trampoline code. I could not verify, without running the code, whether there is a reachable path (e.g., a malicious git server directly reporting a mismatched `host=`/`url=` value during the credential-helper handshake, or a misbehaving proxy) that bypasses that git-level protection and reaches `findGitHubTrampolineAccount` with attacker-controlled origin data. This should be treated as a **finding requiring further dynamic verification**, not a confirmed exploit chain.

### Recommendation
Bind the credential lookup in `findGitHubTrampolineAccount`/`getGitHubCredential` to the repository's actual configured remote origin (captured when the trampoline session/environment is set up for that repository) instead of trusting the `host`/`url` fields supplied fresh on each credential-helper invocation. Reject or prompt-confirm credential requests whose reported origin doesn't match the origin recorded at the start of the git operation, analogous to what `same-origin-filter.ts` already does for `fetch`-based requests.

### Proof of Concept
Conceptual (not verified end-to-end):
1. User has a signed-in GitHub.com account in Desktop.
2. User clones/fetches a repository whose remote is a malicious or compromised git HTTP server.
3. During the HTTP Git protocol exchange, the server (or a proxy in the path) causes the credential-helper request handled by `createCredentialHelperTrampolineHandler` to carry a `host`/`url` whose origin matches `api.github.com` (e.g., via a redirect step git does follow, or a server directly reporting that host in the credential-helper `host=`/`url=` fields). [6](#0-5) 
4. `findGitHubTrampolineAccount` matches that origin against the user's signed-in `github.com` account and returns it. [1](#0-0) 
5. `credWithAccount` hands the account's OAuth `token` back to `git`, which sends it as Basic auth to whatever host it is actually connected to at that point in the exchange — potentially the attacker's server rather than `api.github.com`. [3](#0-2) 

**Uncertainty note:** I was not able to confirm, within the scope of static code search, an exact reachable path in `git`'s HTTP client that presents a mismatched `host` to the credential helper for a request that is actually delivered to a different origin (git's own redirect handling is the primary control here, and I could not test it live). This finding should be verified with a live Devin session capable of running `git`/Desktop against a controlled malicious remote before treating it as confirmed.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-59)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-57)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L220-248)
```typescript
export const createCredentialHelperTrampolineHandler: (
  store: AccountsStore
) => TrampolineCommandHandler = (store: Store) => async command => {
  const firstParameter = command.parameters.at(0)
  if (!firstParameter) {
    return undefined
  }

  const { trampolineToken: token } = command
  const input = parseCredential(command.stdin)

  if (__DEV__) {
    debug(
      `${firstParameter}\n${command.stdin
        .replaceAll(/^password=.*$/gm, 'password=***')
        .replaceAll(/^(.*)$/gm, '  $1')
        .trimEnd()}`
    )
  }

  try {
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
```

**File:** app/src/main-process/same-origin-filter.ts (L1-34)
```typescript
import { OrderedWebRequest } from './ordered-webrequest'

/**
 * Installs a web request filter to prevent cross domain leaks of auth headers
 *
 * GitHub Desktop uses the fetch[1] web API for all of our API requests. When fetch
 * is used in a browser and it encounters an http redirect to another origin
 * domain CORS policies will apply to prevent submission of credentials[2].
 *
 * In our case however there's no concept of same-origin (and even if there were
 * it'd be problematic because we'd be making cross-origin request constantly to
 * GitHub.com and GHE instances) so the `credentials: same-origin` setting won't
 * help us.
 *
 * This is normally not a problem until http redirects get involved. When making
 * an authenticated request to an API endpoint which in turn issues a redirect
 * to another domain fetch will happily pass along our token to the second
 * domain and there's no way for us to prevent that from happening[3] using
 * the vanilla fetch API.
 *
 * That's the reason why this filter exists. It will look at all initiated
 * requests and store their origin along with their request ID. The request id
 * will be the same for any subsequent redirect requests but the urls will be
 * changing. Upon each request we will check to see if we've seen the request
 * id before and if so if the origin matches. If the origin doesn't match we'll
 * strip some potentially dangerous headers from the redirect request.
 *
 * 1. https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API
 * 2. https://fetch.spec.whatwg.org/#http-network-or-cache-fetch
 * 3. https://github.com/whatwg/fetch/issues/763
 *
 * @param orderedWebRequest
 */
export function installSameOriginFilter(orderedWebRequest: OrderedWebRequest) {
```
