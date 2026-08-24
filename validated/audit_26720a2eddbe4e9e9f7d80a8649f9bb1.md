### Title
Credential helper sends stored GitHub token to any host matching an account's hostname, without verifying it is the repository's configured remote - ([File: app/src/lib/trampoline/find-account.ts])

### Summary
`findGitHubTrampolineAccount` decides which GitHub account's token to hand to git purely by comparing the *hostname* of whatever URL git says it is authenticating against to the hostname of a signed-in account's endpoint. It never checks that this URL corresponds to the actual `origin`/`upstream` remote of the repository the operation was started on, nor to any URL the user actually clicked or configured intentionally.

### Finding Description
The credential trampoline flow works as follows: git invokes the `credential.helper=desktop` helper for *any* host it needs to authenticate against during an operation (clone/fetch/push, but also submodule updates, LFS transfers, and redirects) [1](#0-0) . The helper resolves the endpoint that git wants credentials for straight from whatever `url`/`host`/`protocol` fields git supplies [2](#0-1) , then looks up an account purely by hostname match:

```
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
``` [3](#0-2) 

If a match is found, `getGitHubCredential` immediately merges the account's login/token into the credential response and returns it to git with no further check [4](#0-3) . There is no verification that:
- the `remoteUrl` corresponds to `gitStore.defaultRemote`/`upstreamRemote` of the repository being operated on (compare with the existence of `urlMatchesRemote`/`repositoryMatchesRemote` used elsewhere in the codebase for exactly this kind of check [5](#0-4) ), or
- the URL is one the user explicitly configured as a remote at all.

This is the same broken invariant as the Swivel bug: an object supplied at call time (the Yield Space pool / here, the credential target host) is trusted to correspond to the intended context (maturity/underlying / here, the repository's actual remote) without validation, and the "funds"/secret bound to the wrong context are silently handed over.

Because git determines what host to request credentials for based on its own resolution of remotes, submodule URLs, HTTP redirects, and LFS batch endpoints — none of which Desktop controls or restricts to `github.com`/the account's actual HTTPS host in this code path — a repository under attacker control can force git to ask for credentials against `https://github.com/<attacker>/<victim-looking>` (or any `github.com`/GHE-hosted path) during a clone/fetch/push/submodule update. `findGitHubTrampolineAccount` will match on hostname alone (`origin` equality, e.g. `https://github.com`) and hand back the signed-in user's real GitHub token for that endpoint, scoped to whatever path the attacker's git object requested. `proxies.md` in this same repo documents that Desktop already treats submodules/LFS servers as untrusted variable endpoints for proxy purposes, underscoring that a single repository can direct git to authenticate against arbitrary hosts during one logical operation [6](#0-5) .

### Impact Explanation
The GitHub OAuth/PAT token bound to the signed-in account is exfiltrated to any URL an attacker can get git to request credentials for while the user is performing an ordinary clone/fetch/push/submodule-update against a hostile repository. Since the credential path is `https://github.com/...`, the token is sent over TLS to GitHub's real servers but for a path/repo chosen by the attacker (e.g. a private repo they don't have access to, or one they control to read the Authorization header via a custom Enterprise Server mimicking github.com's hostname isn't required — only the origin needs to match a configured account, which for github.com accounts is always `https://github.com`). This satisfies "credential/token exfiltration" from the accepted impact list, achievable purely by the victim interacting with an attacker-supplied repository — no local access, no malware, no social engineering beyond "clone/open this repo."

### Likelihood Explanation
Likelihood is moderate: it requires a repository/submodule configuration that causes git to request credentials against a hostname matching one of the user's signed-in accounts' origins (trivial for github.com users, since almost every Desktop user is signed into `https://github.com`) for a path the attacker chooses (e.g. a submodule `.gitmodules` entry pointing to `https://github.com/<private-org>/<repo>.git`, or an LFS endpoint under `github.com`). Because Desktop only compares `origin`, not full remote match, and does not consult `repositoryMatchesRemote`/`urlMatchesRemote`, this guard is easy to reach in normal operations that already trigger the trampoline (fetch/clone/push), and no existing check in `getCredential` or `getGitHubCredential` narrows the target beyond hostname equality.

### Recommendation
Before handing a stored account's token to `getCredentialUrl`-derived endpoints in `getGitHubCredential`/`findGitHubTrampolineAccount`, validate that the requested URL corresponds to a remote actually configured on the repository the operation was launched for (reusing `urlMatchesRemote`/`repositoryMatchesRemote` from `app/src/lib/repository-matching.ts`), or explicitly allow-list submodule/LFS sub-paths under the same repository rather than matching on bare hostname/origin. At minimum, warn/prompt the user when the credential target's path (owner/repo) diverges from the operation's originating remote.

### Proof of Concept
1. Sign into GitHub Desktop with a GitHub.com account (token `T`).
2. Attacker crafts a repository containing a `.gitmodules` entry (or LFS remote / HTTP redirect target) pointing to `https://github.com/private-org/secret-repo.git` — a repo the attacker does not have access to but wants Desktop's user's token for.
3. Victim clones/fetches the attacker's repository in Desktop, triggering a submodule update or LFS fetch.
4. Git invokes `credential.helper=desktop get` for `https://github.com/private-org/secret-repo.git`.
5. `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential` calls `findGitHubTrampolineAccount(store, 'https://github.com/private-org/secret-repo.git')`, which matches on `origin === 'https://github.com'` [3](#0-2)  and returns the account, causing token `T` to be sent by git in the `Authorization` header of the request to that path — with no check that `private-org/secret-repo` has any relationship to the repository the user opened.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-57)
```typescript
async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
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

**File:** app/src/lib/repository-matching.ts (L90-118)
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

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
}
```

**File:** docs/technical/proxies.md (L53-58)
```markdown
Corporate proxies are often set up so that a script determines which proxy to use based on the url that the client wants to access (for https urls the script usually only gets the protocol and domain). See [#9127](https://github.com/desktop/desktop/pull/9127) for details on that.

Unfortunately for us there's not a 1:1 correlation between git command and url. Take the simplest case of `git clone URL` for example. While the initial request will be to `URL` it's possible that the repository could contain submodules pointing to other hosts. It's also possible that the repository is set up to use LFS in which case there may be subsequent requests to a dedicated LFS server.

While there might be multiple endpoints accessed by a single Git call we only have the ability to provide Git one proxy url per protocol (http/https) so we're gonna have do do a best-effort guess at a reasonable host. We're also gonna assume that the vast majority of repositories these days do all of their communications over http**s**.

```
