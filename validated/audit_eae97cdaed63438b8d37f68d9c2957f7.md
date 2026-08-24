I have enough evidence to write up the finding now.

### Title
Silent remote URL hijack via unvalidated GitHub API `clone_url` in `updateRemoteUrl` - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically runs `git remote set-url` whenever it detects that a repository's location changed on the GitHub side (repo rename/transfer redirect). Its pre-check that decides whether the rewrite is "safe" only verifies that the *scheme* (`https:`/`https:`) of the old and new URL match — it never verifies that the *hostname* of the new URL matches an expected/trusted host. This is structurally the same class of bug as the Golom finding: a guard meant to gate a state-changing action encodes a weaker invariant than what's actually required, so a value that should be rejected (or additionally checked) passes the "pre-check" and the state-changing branch executes anyway.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` in [1](#0-0)  every time Desktop refreshes the GitHub metadata for a repository (e.g. on repository selection or account refresh). It receives `apiRepo`, the raw response of `api.fetchRepository(owner, name)`, and unconditionally trusts its `clone_url` field: [2](#0-1) 

The only gating logic before calling `gitStore.setRemoteURL(...)` is:
```
if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
```
- `protocolsMatch` only compares `URL.parse(url).protocol` (i.e. `"https:"` vs `"https:"`), not the hostname.
- `remoteUrlUnchanged` confirms the user hasn't manually edited the local remote since the GitHub metadata was last cached — it says nothing about the *new* URL's origin.
- `!urlsMatch` is true precisely in the "repo moved" case, which is the intended trigger for this feature (see changelog entry "`[Fixed] Update the remote url when a repository's name changes on GitHub - #8590`" in [3](#0-2) ).

Nowhere in this chain is the hostname of `updatedRemoteUrl` checked against the hostname of the existing remote or the account's configured endpoint. `urlMatchesRemote`/`urlsMatch`, which do check hostname, are only used to *detect a difference* (to decide whether to overwrite), not to *bound what the replacement is allowed to be*. Compare with `sanitizeCloneName` and `resolveWithin`, which are used elsewhere in the codebase specifically to bound untrusted, derived paths/URLs — no equivalent bound exists here for the destination host: [4](#0-3) .

### Impact Explanation
If the JSON returned for `repos/{owner}/{name}` ever contains a `clone_url` pointing at a different, attacker-influenced host — e.g. from a compromised/malicious GitHub Enterprise Server the user is already signed into, or a network path capable of tampering with that specific API response (the "attacker controls...a GitHub API object...or a git remote/proxy response" scenario) — Desktop will silently run `git remote set-url origin <attacker-host>/...` with no user confirmation. All subsequent git operations for that remote route through `envForRemoteOperation(remote.url)` [5](#0-4) , meaning:
- Future `fetch`/`pull` silently pull attacker-controlled repository content into the user's local checkout (silent corruption of what the user later reviews/commits/merges).
- Future `push` sends the user's code to the attacker-controlled host.
- The Git credential-helper trampoline resolves credentials by the *current* remote host, so credentials/tokens tied to that host string may be sent to the new host via the fill/get flow in [6](#0-5) .

This is a silent, unattended remote rewrite with no user-visible diff or confirmation dialog — the user only sees "repository refreshed."

### Likelihood Explanation
This code path runs automatically and periodically (repository selection, account refresh) without any user interaction, so no unnatural user steps are required once the precondition (attacker-influenced `clone_url` for the specific `owner/name` the user already has configured) is met. The precondition is narrower than a fully unauthenticated remote MITM (it requires a compromised/malicious GHE instance or a way to tamper with that specific API response), but it does not require local/physical access, admin rights, prior malware, or leaked credentials — it fits the "attacker controls...a GitHub API object...or a git remote/proxy response" category from the accepted impact list. Likelihood is Medium: the gap is real and exploitable given the stated threat model, but requires control of the API response, not merely control of a cloned repo.

### Recommendation
Before calling `gitStore.setRemoteURL`, additionally validate that the hostname of `updatedRemoteUrl` matches the hostname of the account/endpoint the repository is associated with (e.g. compare against `gitHubRepository.endpoint`'s host, not just protocol). If the hostnames differ, treat it the same as a protocol change and skip the automatic rewrite, requiring explicit user action instead.

### Proof of Concept
1. User has repository `origin` = `https://ghe.example.com/acme/widgets.git`, associated GitHub Enterprise account at `https://ghe.example.com`.
2. The GHE instance (compromised, or a proxy/response the attacker can influence per the stated threat model) responds to `GET repos/acme/widgets` with `clone_url: "https://attacker.evil/acme/widgets.git"`.
3. `repositoryWithRefreshedGitHubRepository` calls `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`.
4. `protocolsMatch` → true (`https:` === `https:`); `remoteUrlUnchanged` → true (local remote still matches last-cached `cloneURL`); `urlsMatch` → false (owner/name/hostname differ from `attacker.evil`) → `!urlsMatch` → true.
5. `gitStore.setRemoteURL('origin', 'https://attacker.evil/acme/widgets.git')` executes silently.
6. Next fetch/push routes to `attacker.evil` via `envForRemoteOperation`, per [7](#0-6) .

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
```typescript
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
}
```

**File:** changelog.json (L2761-2761)
```json
      "[Fixed] Update the remote url when a repository's name changes on GitHub - #8590",
```

**File:** app/src/lib/remote-parsing.ts (L72-88)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
```

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
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

**File:** app/src/lib/git/fetch.ts (L39-48)
```typescript
export async function fetch(
  repository: Repository,
  remote: IRemote,
  progressCallback?: (progress: IFetchProgress) => void,
  isBackgroundTask = false
): Promise<void> {
  let opts: IGitStringExecutionOptions = {
    successExitCodes: new Set([0]),
    env: await envForRemoteOperation(remote.url),
  }
```
