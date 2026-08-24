The external report's broken invariant is: **a single, broadly-scoped trust grant (DEFAULT_ADMIN_ROLE) is handed out without any additional scoping, confirmation step, or path-specific validation.** The closest verifiable analog in this codebase is in GitHub Desktop's git-credential trampoline, where any git subprocess request for credentials is satisfied by matching **only the URL origin** against a stored account, with no verification that the request corresponds to the repository/operation the user actually initiated.

### Title
Git credential helper grants stored account tokens to any same-origin credential request without repository-scoped confirmation - (File: `app/src/lib/trampoline/find-account.ts`)

### Summary
`findGitHubTrampolineAccount` matches a stored `Account` purely by comparing `new URL(getHTMLURL(a.endpoint)).origin` to the origin of the credential-request URL, with no check on path, repository owner, or which remote the user is actually interacting with. [1](#0-0) 
This match result is then used by the credential helper trampoline to silently hand over the user's real OAuth token for *any* git operation that reaches this code path. [2](#0-1) 

### Finding Description
When the `desktop-credential-helper-trampoline` process asks the app for a credential (via the `get` command), `getCredential` first tries `getGitHubCredential`, which calls `findGitHubTrampolineAccount(store, endpoint)`. That function iterates all signed-in accounts and returns the first one whose endpoint origin matches the request's origin — it does not verify that the requested URL path belongs to a repository the account should have access to, nor that the request originates from the specific clone the user opened. [3](#0-2) 
`getEndpointKind` further widens the trust surface: if no account already matches, and the host advertises a `WWW-Authenticate: realm="GitHub"` header, or an active network probe (`isGitHubHost`) succeeds, the endpoint is classified as `'enterprise'` and the user is prompted to sign in — but once any account for that origin exists, all *subsequent* credential requests to the same origin are auto-approved with no further confirmation. [4](#0-3) 
Git itself decides when to invoke the credential helper — this happens for every HTTPS remote git touches during a single `git` invocation, including transitively for submodules recorded in a repository's `.gitmodules` file (attacker-controlled content in a cloned repo) if Desktop or the user performs a submodule-inclusive operation. Because the trampoline's matching logic is origin-only and account-wide rather than scoped to the specific remote the user explicitly added, any additional same-origin remote surfaced by repository content (e.g., a submodule pointing at a different path/owner under `github.com`) transparently receives the user's real token without a distinct prompt, confirmation, or path-scoping check.

### Impact Explanation
This enables a confused-deputy pattern: a malicious/untrusted repository can, via content it controls (e.g. `.gitmodules` submodule URLs, or `insteadOf` records — subject to Desktop's submodule/config handling, which I was not able to fully trace in this pass), cause the credential helper to authenticate outbound git requests to arbitrary same-origin paths using the victim's real GitHub/GHE token, without the user being shown which repository/path the token is actually being used against. This does not require the attacker to have local access, admin rights, or pre-existing credentials — it only requires the victim to clone/fetch the attacker's repository, matching the report's threat model (attacker controls cloned/fetched repository content). The practical impact is unauthorized use of the victim's authenticated session to probe or fetch content the attacker would not otherwise be authorized to access, and silent credential reuse outside the scope the user intended.

### Likelihood Explanation
Moderate-to-uncertain. The origin-only matching logic is confirmed in code and is unconditional whenever `getGitHubCredential` succeeds — there is no gate requiring the request to correspond to the specific repository the user opened. What I could **not** fully verify with the tools available is the exact end-to-end trigger: whether GitHub Desktop's normal clone/fetch/checkout flow ever invokes recursive submodule fetches (or other git operations that read attacker-controlled `.gitmodules`/config) without an explicit, separate user action per new remote. Confirming that trigger path would require deeper tracing of Desktop's submodule and git-operation code, which was outside the reachable evidence in this session.

### Recommendation
Scope trampoline credential responses to the specific repository/remote the user explicitly added or is actively operating on (e.g., pass down the originating repository path/remote name through `withTrampolineEnv` and validate it against the credential request), rather than trusting any request that merely shares an origin with a previously signed-in account. Additionally, require explicit user confirmation before silently supplying stored credentials to a *newly encountered* remote/path under an already-trusted origin (analogous to requiring an explicit, confirmed step before extending a broad privilege, as recommended for `DEFAULT_ADMIN_ROLE` in the original report).

### Proof of Concept
Not independently reproduced end-to-end. Based on code review only:
1. Victim is signed into `github.com` in GitHub Desktop, token stored via `AccountsStore`/`TokenStore` (keytar). [5](#0-4) 
2. Victim clones/fetches an attacker-authored public repository that references (via `.gitmodules` or other git-config content read during a git operation) a second `https://github.com/...` URL under a different owner/path.
3. When git invokes the credential helper for that second URL, `findGitHubTrampolineAccount` matches solely on origin (`github.com`) and returns the victim's account, and `getGitHubCredential` returns the victim's real token — with no indication to the user that credentials were reused for a path/repo they never explicitly opened. [1](#0-0) [2](#0-1) 

I was unable to confirm within this session whether Desktop's UI/CLI paths ever perform this kind of transitive git operation without a distinct, separate user-initiated action for the second remote — this is the key open question that would determine whether this is exploitable in practice versus a defense-in-depth gap.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-170)
```typescript
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
```

**File:** app/src/lib/stores/token-store.ts (L1-19)
```typescript
import * as keytar from 'keytar'

function setItem(key: string, login: string, value: string) {
  return keytar.setPassword(key, login, value)
}

function getItem(key: string, login: string) {
  return keytar.getPassword(key, login)
}

function deleteItem(key: string, login: string) {
  return keytar.deletePassword(key, login)
}

export const TokenStore = {
  setItem,
  getItem,
  deleteItem,
}
```
