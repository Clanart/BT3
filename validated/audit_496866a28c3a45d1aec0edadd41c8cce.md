### Title
Argument injection via unsanitized GitHub API `clone_url` passed to `git remote add` / `git remote set-url` - ([File: app/src/lib/git/remote.ts])

### Summary
`addRemote()` and `setRemoteURL()` in `app/src/lib/git/remote.ts` build git command-line argument arrays that place an externally-derived URL directly after the remote name, with no `--` end-of-options separator. This is inconsistent with `clone()` in `app/src/lib/git/clone.ts`, which explicitly inserts `'--'` before the URL to prevent Git from interpreting a value that starts with `-` as a command-line flag. The URL fed into these unprotected paths can originate from a GitHub API response object (`IAPIRepository`/`IAPIFullRepository.clone_url`), which is treated as trusted data even though it comes from a server response.

### Finding Description [1](#0-0) 
`addRemote()` runs `git(['remote', 'add', name, url], ...)` with no `--` separator before `url`. [2](#0-1) 
`setRemoteURL()` runs `git(['remote', 'set-url', name, url], ...)` with the same missing protection.

By contrast, `clone()` explicitly guards against this class of bug: [3](#0-2) 
`args.push('--', url, path)` is used before invoking `git`, which is the documented mitigation for Git argument/option injection when a value is attacker-influenced.

The unprotected functions are reachable with data taken directly from GitHub API repository objects:
- `updateRemoteUrl()` reads `apiRepo.clone_url` (from an `IAPIRepository` returned by the API) and calls `gitStore.setRemoteURL(...)` with it when the recorded GitHub `clone_url` no longer matches the local remote: [4](#0-3) 
- `_convertRepositoryToFork()` in `app-store.ts` passes `fork.clone_url` (an `IAPIFullRepository` field) straight into `gitStore.setRemoteURL(defaultRemoteName, fork.clone_url)` and also `gitStore.ensureUpstreamRemoteURL(remoteUrl)`, which internally calls `addRemote`/`setRemoteURL`: [5](#0-4) [6](#0-5) 

None of these call sites validate that `clone_url` begins with a benign scheme (`https://`, `git@`, `ssh://`) before it is spliced into the argument vector; they only compare hostnames/owners/names for change-detection purposes (`urlMatchesRemote`), not for injection-safety.

### Impact Explanation
If a `clone_url` value delivered through an API response (e.g. from a GitHub Enterprise Server instance the user has added an account for, or a MITM/compromised API endpoint) begins with `-`, Git may interpret it as an option rather than a positional URL argument for `remote add`/`remote set-url`. Depending on the Git version and which flag is smuggled, this can corrupt the remote configuration silently (pointing `origin` or `upstream` at an unintended location, changing fetch/push behavior) or, in more severe cases, be chained with other Git option-parsing primitives to influence subsequent network operations against attacker-controlled infrastructure — which then flows into every future fetch/push/pull for that repository, i.e. corrupting what the user pushes/pulls without their knowledge.

### Likelihood Explanation
The exact severity depends on how much control an attacker practically has over `clone_url` in a real API response — GitHub.com validates repository/owner names strictly, limiting this on dotcom, but Desktop also supports GitHub Enterprise Server accounts whose API responses are less centrally controlled, and the `IAPIRepository` type imposes no format constraint on `clone_url` before it reaches these functions. The core issue — the missing `--` separator that `clone()` explicitly uses — is a genuine inconsistency in the codebase's own defensive pattern, independent of exactly how untrusted the URL is in every deployment.

### Recommendation
Add a `'--'` separator before the URL argument in both `addRemote()` and `setRemoteURL()` in `app/src/lib/git/remote.ts`, mirroring the existing protection in `clone()`, e.g. `git(['remote', 'add', name, '--', url], ...)` (note: verify exact Git syntax support for `remote add`/`set-url --` separator, as it differs from `clone`) or otherwise validate/reject URLs beginning with `-` before they are passed to these functions.

### Proof of Concept
1. An account is configured against a GitHub Enterprise Server (or a compromised/MITM API endpoint) that returns a repository object where `clone_url` (or `ssh_url`) is set to a string beginning with `-`, e.g. `-oProxyCommand=...` or another Git-recognized flag.
2. `updateRemoteUrl()` detects that this differs from the previously known GitHub `clone_url` for the repo and calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` — [7](#0-6) .
3. This flows to `setRemoteURL(repository, name, url)` in `app/src/lib/git/remote.ts`, which runs `git(['remote', 'set-url', name, url], ...)` without a `--` separator — [2](#0-1) .
4. Because the value is not preceded by `--`, Git may parse the leading-dash string as an option rather than the URL, unlike the equivalent `clone()` code path which explicitly protects against this.

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

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L8965-8991)
```typescript
  /**
   * Converts a local repository to use the given fork
   * as its default remote and associated `GitHubRepository`.
   */
  public async _convertRepositoryToFork(
    repository: RepositoryWithGitHubRepository,
    fork: IAPIFullRepository
  ): Promise<Repository> {
    const gitStore = this.gitStoreCache.get(repository)
    const defaultRemoteName = gitStore.defaultRemote?.name
    const remoteUrl = gitStore.defaultRemote?.url
    const { endpoint } = repository.gitHubRepository

    // make sure there is a default remote (there should be)
    if (defaultRemoteName !== undefined && remoteUrl !== undefined) {
      // update default remote
      if (await gitStore.setRemoteURL(defaultRemoteName, fork.clone_url)) {
        await gitStore.ensureUpstreamRemoteURL(remoteUrl)
        // update associated github repo
        return this.repositoriesStore.setGitHubRepository(
          repository,
          await this.repositoriesStore.upsertGitHubRepository(endpoint, fork)
        )
      }
    }
    return repository
  }
```

**File:** app/src/lib/stores/git-store.ts (L1358-1380)
```typescript
  /**
   * Sets the upstream remote to a new url,
   * creating the upstream remote if it doesn't already exist
   *
   * @param remoteUrl url to be used for the upstream remote
   */
  public async ensureUpstreamRemoteURL(remoteUrl: string): Promise<void> {
    await this.performFailableOperation(async () => {
      try {
        await addRemote(this.repository, UpstreamRemoteName, remoteUrl)
      } catch (e) {
        if (
          e instanceof DugiteError &&
          e.result.gitError === GitError.RemoteAlreadyExists
        ) {
          // update upstream remote if it already exists
          await setRemoteURL(this.repository, UpstreamRemoteName, remoteUrl)
        } else {
          throw e
        }
      }
    })
  }
```
