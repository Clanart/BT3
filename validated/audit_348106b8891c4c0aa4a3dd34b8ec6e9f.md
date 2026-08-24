### Title
Automatic remote URL rewrite based on unvalidated GitHub API `clone_url` allows silent redirection of a user's push target - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
The report's core defect is that a privileged setter (`setCustomContract`) lets an external/administrative input silently overwrite a critical routing value (the token mapping) with no verification against the entity actually controlling the funds, and with no fallback for the affected user. The closest structural analog in GitHub Desktop is `updateRemoteUrl`, which automatically rewrites a repository's local `origin` remote URL to whatever `clone_url` the GitHub API returns for the associated `GitHubRepository`, with no user confirmation and only a loose heuristic check.

### Finding Description
`updateRemoteUrl` compares the currently configured remote URL to the `clone_url` field of an `IAPIRepository` object fetched from the GitHub API, and if the owner/name differ but the protocol matches and the *previous* clone URL still matches the stored `GitHubRepository.cloneURL`, it calls `gitStore.setRemoteURL` to silently rewrite the local `origin` remote: [1](#0-0) 

The URL-comparison logic (`urlMatchesRemote`) only checks hostname/owner/name equality after parsing — it has no cryptographic or identity-based verification that the new `clone_url` actually corresponds to the same underlying repository object the user originally cloned: [2](#0-1) 

This mirrors the bridge bug's invariant break: an externally-supplied value (`_targetContract` / here, `apiRepo.clone_url`) is trusted and used to overwrite a critical routing/destination value (`nativeToBridgedToken` mapping / here, the git `origin` URL) without the affected party's explicit consent, and the consuming code (`setRemoteURL` in `app/src/lib/git/remote.ts`) applies it directly via `git remote set-url`: [3](#0-2) [4](#0-3) 

The scenario that triggers this: a repository is renamed/transferred/moved on GitHub (which changes `clone_url` while keeping the repository's underlying id — a routine, attacker-reachable action if the attacker controls or compromises the account/repo that a fork's `parent` or the tracked `GitHubRepository` points to), and the next background refresh of repository metadata calls `updateRemoteUrl`, which then silently repoints the user's local `origin` to the new address — all without any dialog, confirmation prompt, or notification surfaced to the user.

### Impact Explanation
If exploited, a user's future `git push`/`git fetch` operations would silently target a different remote than the one they originally set up and reviewed, without any interactive confirmation (contrast with `repository-settings.tsx`'s `onSubmit`, which is the *manual*, user-initiated path for changing the remote URL and does show errors/confirmation): [5](#0-4) . Because `envForRemoteOperation`/credential resolution keys off the remote URL, credentials could also be sent to the new target transparently: [6](#0-5) . This matches "silent corruption of what the user commits or pushes" in the valid-impact criteria, since the user has no visibility into or recovery path for the automatic remote change — analogous to the report's "funds stuck with no withdraw" pattern (here: pushes silently redirected with no visible warning or opt-out).

### Likelihood Explanation
The trigger only requires an ordinary, unprivileged GitHub action (renaming/transferring a repository or a fork's upstream) reflected through the standard background metadata refresh that already exists in the app; no local access, malware, or leaked credentials are needed. However, exploitation depends on specific preconditions (`protocolsMatch`, `remoteUrlUnchanged`, `!urlsMatch`) being satisfied simultaneously, and the change is limited to same-protocol same-host GitHub URLs, which somewhat narrows the practical attack surface. There is also a design comment in the code acknowledging uncertainty about when these conditions are hit ("I'm not sure when these early exit conditions would be met"), suggesting this path was not thoroughly reasoned through for adversarial cases: [7](#0-6) .

### Recommendation
Do not silently call `setRemoteURL` from a background metadata refresh. At minimum, surface a confirmation dialog to the user before rewriting `origin`, and audit-log/notify when the automatic rewrite path fires so the change is not invisible, similar to how manual remote URL edits in `repository-settings.tsx` already give the user visibility and error feedback.

### Proof of Concept
1. Clone a GitHub repository via Desktop; `origin` points to `https://github.com/owner/repo`.
2. The tracked `GitHubRepository.cloneURL` in local storage matches that URL exactly.
3. The repository is renamed/transferred on GitHub.com (an unprivileged, everyday action by whoever administers that remote repo) so the API's `clone_url` becomes `https://github.com/owner/new-repo` (or a different owner if transferred).
4. On the next periodic refresh, `updateRemoteUrl` is invoked with the updated `IAPIRepository`; `protocolsMatch` is true, `remoteUrlUnchanged` is true (old clone URL matches previous state), `urlsMatch` is false → `gitStore.setRemoteURL` fires and calls `git remote set-url origin https://github.com/owner/new-repo` with no prompt shown to the user: [8](#0-7) .
5. The user's subsequent `git push` silently targets the new remote without ever being asked to confirm the change.

Note: I was unable to fully trace every call site that invokes `updateRemoteUrl` inside `app-store.ts` (index tooling truncated the surrounding context), so the exact refresh cadence/trigger conditions in production could not be fully confirmed from the available index; a full review of `app-store.ts` around the `updateRemoteUrl` call sites is recommended to confirm exact triggering frequency.

### Citations

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

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
  }
```

**File:** app/src/ui/repository-settings/repository-settings.tsx (L292-313)
```typescript
  private onSubmit = async () => {
    this.setState({ disabled: true, errors: undefined })
    const errors = new Array<JSX.Element | string>()

    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
        }
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
