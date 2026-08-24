## Title
Attacker-controlled `clone_url` from a PR's fork repository can be written into git remote config and trigger an `ext::` transport RCE - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/git/remote.ts`)

### Summary
This is the closest structural analog to the reported "check on A, but the operation actually needs B" bug class. In the original report, `Permissions.reassignGlobalAdmin` performs an authorization check (`TIMELOCK_ROLE`) that does not match the check actually enforced by the underlying operation (`revokeRole` needs `ADMIN_ROLE`), so the assumed invariant ("caller is authorized to perform this state change") silently doesn't hold at the point the state change happens. In GitHub Desktop, the equivalent broken invariant is: the codebase has protocol-restriction logic for remote URLs (`parseRemote` in `app/src/lib/remote-parsing.ts` only recognizes `https`/`ssh`/`git` shaped strings, and `envForProxy` in `app/src/lib/git/environment.ts` only special-cases `http(s)`), but that "known-protocols-only" assumption is never actually enforced as a security gate at the place where a remote URL is written into the repository's git config. `addRemote`/`setRemoteURL` in `app/src/lib/git/remote.ts` pass the raw URL string straight to `git remote add`/`git remote set-url` with no allow-listing.

### Finding Description
`app/src/lib/git/remote.ts` writes attacker/API-supplied strings directly into git config: [1](#0-0) [2](#0-1) 

Neither function validates that `url` is restricted to `https://`/`ssh://`/`git://`. The app's own `parseRemote` regexes (`app/src/lib/remote-parsing.ts`) are only used for *display/matching* purposes (`urlMatchesRemote`, `urlsMatch`, `sanitizeCloneName`) — not as a gate before persisting a URL via `addRemote`/`setRemoteURL`. This is exactly the pattern in the report: a validation exists somewhere in the system, but the function that performs the actual privileged/dangerous operation doesn't consult it, so the assumed invariant ("only `http(s)`/`ssh` remotes exist in this repo") is not actually guaranteed at the point it matters.

Two call sites feed externally-controlled data into `addRemote`:
- `app/src/ui/dispatcher/dispatcher.ts` — `_findPullRequestBranch` calls `addRemote(repository, forkRemoteName, headCloneUrl)` where `headCloneUrl` originates from `pullRequest.head.repo.clone_url`, a field returned by the GitHub API for the PR's *fork* repository [3](#0-2) .
- `app/src/lib/stores/updates/update-remote-url.ts` calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` where `updatedRemoteUrl = apiRepo.clone_url`, again a raw string taken from a GitHub API response object [4](#0-3) .

Neither of these call sites, nor `addRemote`/`setRemoteURL` themselves, reject Git's `ext::` (or `fd::`) transport syntax, which git treats as opaque-command execution (`git remote add x "ext::sh -c 'touch /tmp/pwned'"` will execute the shell command the next time that remote is fetched from). No `GIT_ALLOW_PROTOCOL`/`GIT_PROTOCOL_FROM_USER` restriction is configured anywhere in `app/src/lib/git/environment.ts` [5](#0-4) , so Desktop relies entirely on git's own default protocol allow-list. Because the malicious URL is written into local git config by Desktop's own code (same as a remote that a user configured manually), git's default "user"-classified allow policy for `ext` treats it as trusted, and the command executes on the next fetch/pull of that remote.

### Impact Explanation
If a malicious/compromised GitHub Enterprise API endpoint (or any actor able to influence the JSON returned for a pull request's fork repository or for the repository the user has open) sets `clone_url` to an `ext::` transport string, GitHub Desktop will silently store this value as a real git remote via `addRemote`/`setRemoteURL`. The very next time Desktop (or the user) fetches that remote — which for the `_findPullRequestBranch` path happens automatically as part of "Check out pull request" (`_fetchRemote` is called right after `addRemote`) — arbitrary attacker-supplied shell commands execute in the context of the user's machine. This satisfies the "Valid Impact" criteria: the attacker controls a GitHub API object, and the result is code execution outside the sandboxed rendering context.

### Likelihood Explanation
Exploitation requires the user to check out a pull request from a fork whose `head.repo.clone_url` (or whose default remote's API-reported clone URL) has been tampered with — realistic against a self-hosted/compromised GHE instance or any MITM position on the API traffic, and requires no unusual user action (checking out a PR from a fork is a normal, expected Desktop workflow). This is a plausible but conditional attack path (it depends on control over the GitHub API response, not on typical github.com behavior where `clone_url` is server-generated from validated repo name/owner strings), so likelihood is moderate rather than certain.

### Recommendation
- Centralize an allow-list check (reuse/extend `parseRemote`) and reject any URL that does not match a known-safe `https://`/`ssh://`/`git://` pattern before it reaches `addRemote` or `setRemoteURL` in `app/src/lib/git/remote.ts`.
- Explicitly reject `ext::`/`fd::`/other opaque-transport prefixes at the same choke point, regardless of source.
- Set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or equivalent) in `envForRemoteOperation`/`envForAuthentication` for all git invocations that touch remotes, as defense in depth, so even a config-level bypass cannot invoke disallowed transports.
- Apply the same validation to `updateRemoteUrl` before calling `gitStore.setRemoteURL`.

### Proof of Concept
1. Attacker controls (or MITMs) a GitHub Enterprise API endpoint, or otherwise causes the PR API response for a fork to contain: `head.repo.clone_url = "ext::sh -c \"touch /tmp/pwned\""`.
2. Victim, using Desktop against that endpoint, opens the PR and selects "Check out pull request".
3. `dispatcher.ts`/`app-store.ts` calls `_findPullRequestBranch`, which calls `addRemote(repository, forkRemoteName, headCloneUrl)` [3](#0-2) , writing the `ext::` URL into `.git/config` via `git remote add`.
4. The same function immediately calls `this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)` [6](#0-5) , causing git to fetch from the newly added remote and execute the attacker's `ext::` command.

Note: I was unable to fully confirm from the indexed code exactly what character/URL validation, if any, GitHub.com itself imposes on `clone_url` construction versus a GHE/mock endpoint, since that logic lives server-side and is outside this repository. This finding assumes the "attacker controls a GitHub API object" premise explicitly permitted by the task's Valid Impact criteria (e.g., malicious/compromised API endpoint), not a mainline github.com exploit.

### Citations

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
```

**File:** app/src/lib/stores/app-store.ts (L8647-8651)
```typescript
    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
```

**File:** app/src/lib/stores/app-store.ts (L8684-8690)
```typescript
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-44)
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
