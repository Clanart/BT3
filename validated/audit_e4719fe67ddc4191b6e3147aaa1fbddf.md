Based on the investigation, the closest verifiable analog to the reported "approved-but-unconsented resource is bonded via an attacker-controlled proxy object" pattern is in GitHub Desktop's Git credential trampoline, where a token approved once for GitHub.com/GHE is silently re-used for **any** host-matching git operation triggered by attacker-controlled repository content (submodule URLs), without per-repository consent.

### Title
Attacker-controlled submodule/LFS URLs cause silent reuse of the user's stored GitHub token against attacker-chosen repositories - (File: app/src/lib/trampoline/find-account.ts)

### Summary
When a user signs in to GitHub Desktop, the app stores their OAuth token and approves the internal Git credential-helper trampoline to hand it to Git for any operation. The trampoline decides whether to inject the real token purely based on **origin equality** between the URL Git is authenticating against and the signed-in account's endpoint, with no check that the URL corresponds to the repository the user actually opened/consented to. Because Desktop performs `git clone --recursive` and `git submodule update --init --recursive`, an attacker who authors a public repository can embed a `.gitmodules` entry (or LFS remote) pointing at an arbitrary `github.com`/GHE path, and Desktop's credential helper will transparently supply the victim's real token to authenticate that hidden request.

### Finding Description
The credential-helper flow is:
1. `createCredentialHelperTrampolineHandler` receives a `get` credential request from Git and calls `getCredential` → `getGitHubCredential`. [1](#0-0) 
2. `getGitHubCredential` resolves the account to use via `findGitHubTrampolineAccount`, which matches solely by comparing the *origin* of the credential-request URL with the origin of a stored account's HTML URL — no binding to which repository/remote the user actually opened in Desktop: [2](#0-1) 
3. If an account matches, the real `login`/`token` are merged straight into the credential and handed back to Git: [3](#0-2) 
4. Clone and submodule-update operations recurse automatically and unconditionally into any submodule URL declared inside the (attacker-controlled) repository content, each of which triggers its own credential-helper round trip: [4](#0-3) [5](#0-4) 

This mirrors the reported flaw's structure: a party that controls one field of an otherwise-legitimate object (the disputeModule address in the report; here, the submodule/LFS URL embedded in a cloned repository) can redirect an operation that was authorized in a different, narrower context (the user approving the response module for *their own* request; here, the user signing in and approving the trampoline for *their own* git operations) so that it acts against a target the approving party never intended (bonding another user's tokens; here, authenticating to an attacker-chosen repository/path with the victim's real credentials).

### Impact Explanation
Since the origin check only validates protocol+host+port and not the specific repository path, an attacker-authored public repository can silently cause Desktop to authenticate — using the victim's real GitHub token — against any repository on `github.com`/the victim's GHE instance, including private repositories the victim didn't intend to touch in that session. This can be used to: probe private-repository existence/access (timing/success of the credential exchange leaks access information), or trigger authenticated Git/LFS requests against attacker-chosen targets that get logged/attributed to the victim's account, all without any Desktop UI prompt, because the origin match causes the flow to bypass the "prompt for GitHub sign-in" branch entirely.

### Likelihood Explanation
Likelihood is elevated by the fact that `clone` always passes `--recursive` and `updateSubmodulesAfterOperation` always runs `submodule update --init --recursive` for GitHub-hosted repositories, meaning any user who clones or fetches an attacker-authored public repository (a very ordinary, low-friction action) automatically exercises this path with no additional interaction needed. [6](#0-5) [7](#0-6) 

### Recommendation
- Short term: scope credential auto-fill in `findGitHubTrampolineAccount`/`getGitHubCredential` to the specific repository (and, where relevant, submodule allowlist) the user explicitly opened/cloned, rather than matching on origin alone; require explicit confirmation before silently authenticating to a submodule/LFS host that differs from the top-level remote's repository path.
- Long term: define an access-control specification for the trampoline describing exactly which git subprocess invocations are permitted to receive a stored token, and add regression tests asserting that credential fill is denied for URLs not derived from the repository/operation the user initiated.

### Proof of Concept
1. Victim is signed in to GitHub Desktop with a GitHub.com account.
2. Attacker publishes a public repository containing a `.gitmodules` entry: `url = https://github.com/victim-org/private-repo`.
3. Victim clones the attacker's repository in Desktop (`clone.ts` runs with `--recursive`).
4. During submodule initialization, Git requests credentials for `https://github.com/victim-org/private-repo`; `findGitHubTrampolineAccount` matches the origin `https://github.com` against the victim's stored account and `getGitHubCredential` returns the victim's real token/login without any prompt.
5. Git attempts to fetch `victim-org/private-repo` using the victim's own credentials, silently confirming access (or attributing the request) to a target the victim never asked to interact with.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-48)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)
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

**File:** app/src/lib/git/clone.ts (L86-93)
```typescript
  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/git/submodule.ts (L29-54)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

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
