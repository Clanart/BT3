## Finding

### Title
Unsanitized GitHub API `clone_url` reaches `git remote add`/`set-url` without an end‑of‑options guard, enabling remote‑URL argument/transport injection - (File: `app/src/lib/git/remote.ts`)

### Summary
When GitHub Desktop resolves a pull request's fork remote or a fork's upstream remote, it takes the `clone_url` field straight out of a GitHub API response object and forwards it verbatim to `git remote add` / `git remote set-url`. Unlike `clone()`, which explicitly terminates option parsing with `--` before the URL argument, `addRemote()` and `setRemoteURL()` do not, so a value returned by the API that happens to look like a CLI flag or a dangerous git transport (e.g. `ext::`) is passed through unguarded.

### Finding Description
`_findPullRequestBranch` and `addUpstreamRemoteIfNeeded`/`ensureUpstreamRemoteURL` treat `pullRequest.head.repo.clone_url` and `parent.cloneURL` — both values sourced from a GitHub API response — as a trusted git remote URL: [1](#0-0) [2](#0-1) 

Those values flow into `addRemote()`/`setRemoteURL()`: [3](#0-2) 

Notice these git invocations pass `name` and `url` as plain positional arguments with no `--` end-of-options marker. Compare this with `clone()`, which is aware of this exact hazard and explicitly guards against it: [4](#0-3) 

Because `addRemote`/`setRemoteURL` skip that guard, a `clone_url` value beginning with `-` could be interpreted as a `git remote add`/`set-url` option rather than a URL (argument injection), and once such a URL is stored as a remote, any subsequent `fetch`/`pull`/`push` against it is fully attacker-controlled — including "smart" transports like `ext::` that spawn an arbitrary local command when Git dereferences the remote. `updateRemoteUrl()` similarly rewrites the default remote's URL to the API's `apiRepo.clone_url` once a same-protocol/same-repo heuristic passes, without any allow-list on the URL's protocol or leading characters: [5](#0-4) 

The broken invariant is: *"a URL value ultimately sourced from an untrusted or attacker-influenced object may be safely handed to `git remote add`/`set-url`."* This holds under the report's "GitHub API object" attacker model — e.g., a malicious or compromised GitHub Enterprise server, or a crafted repository fork whose GitHub-generated metadata is proxied/manipulated in transit — because none of the call sites feeding `addRemote`/`setRemoteURL` validate protocol or leading characters, unlike `clone()`.

### Impact Explanation
If exploited, this allows an attacker who controls (or can influence) a GitHub API repository object consumed by Desktop to plant a malicious remote URL in the user's local git config. Depending on the payload this can lead to: (a) argument injection into `git remote add`/`set-url`, or (b) full command execution the next time Desktop (or the user, unaware) fetches/pushes that remote via a dangerous transport helper — i.e., code execution outside the sandboxed UI, corrupting the integrity of what the user later pushes/pulls.

### Likelihood Explanation
Exploitation requires the victim to be viewing/opening a pull request or fork whose `clone_url`/`cloneURL` was supplied by a non-standard-behaving GitHub API endpoint (e.g., GHE instance under attacker influence, or a MITM/manipulated response), which is a narrower channel than a normal GitHub.com PR (where `clone_url` is server-generated from the owner/repo name and thus constrained). Still, no defensive check exists at the `addRemote`/`setRemoteURL` layer itself — the safety currently relies entirely on GitHub.com's own generation of well-formed clone URLs, not on Desktop's own validation, which is the same "trust the observed value instead of verifying/sanitizing it" pattern as the referenced report.

### Recommendation
Add the same `--` end-of-options separator used in `clone()` to `addRemote()` and `setRemoteURL()` in `app/src/lib/git/remote.ts`, and additionally validate that any URL originating from a GitHub API object (`clone_url`, `cloneURL`) matches an allow-listed `https://`/`ssh://`/`git@` pattern (reusing `parseRemote()`/`remote-parsing.ts`) before it is ever passed to `git remote add`/`set-url`/`fetch`, rejecting anything that isn't a well-formed, expected-protocol remote.

### Proof of Concept
1. Point Desktop at a GitHub Enterprise endpoint (or intercept/replace the API response) so that a pull request's `head.repo.clone_url` (or a fork's `parent.cloneURL`) is returned as `ext::sh -c "touch /tmp/pwned"` instead of a normal HTTPS/SSH URL.
2. User opens that PR in Desktop / Desktop calls `_checkoutPullRequest` → `_findPullRequestBranch`, which calls `addRemote(repository, forkRemoteName, headCloneUrl)` (`app/src/lib/stores/app-store.ts:8651`), which in turn calls `git(['remote', 'add', name, url], ...)` (`app/src/lib/git/remote.ts:34`) with no `--` separator and no protocol validation.
3. The malicious remote is now configured in the user's `.git/config`; the immediately following `_fetchRemote` call (`app/src/lib/stores/app-store.ts:8686`) causes Git to invoke the `ext::` transport helper, executing the attacker's command on the user's machine.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8645-8652)
```typescript
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
```

**File:** app/src/lib/stores/git-store.ts (L1347-1356)
```typescript
    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
  }
```

**File:** app/src/lib/git/remote.ts (L28-64)
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

/** Removes an existing remote, or silently errors if it doesn't exist */
export async function removeRemote(
  repository: Repository,
  name: string
): Promise<void> {
  const options = {
    successExitCodes: new Set([0, 2, 128]),
  }

  await git(
    ['remote', 'remove', name],
    repository.path,
    'removeRemote',
    options
  )
}

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
