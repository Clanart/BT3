### Title
Missing `--` end-of-options guard in `addRemote()` allows git argument injection via attacker-influenced remote URLs - ([File: app/src/lib/git/remote.ts])

### Summary
The external report's broken invariant is "no validation of an attacker-influenced value before it is consumed by a critical execution path" (pump computes a trade without checking the actual reserves match the assumed/quoted ones). The closest verifiable Desktop analog is `addRemote()` in [1](#0-0) , which passes a URL string directly to `git remote add` without the `--` end-of-options marker that the rest of the codebase uses to stop URL-like strings from being reinterpreted as CLI flags.

### Finding Description
`clone()` explicitly guards against this class of bug: [2](#0-1)  inserts `'--'` before the `url` argument specifically so a hostile value cannot be parsed as a git option (e.g. `--upload-pack=`).

`addRemote()` has no equivalent guard: [3](#0-2) 

The `url` parameter reaches `git(['remote', 'add', name, url], ...)` positionally, with nothing preventing a string beginning with `-` from being parsed as a git CLI switch rather than a literal remote URL. This is invoked from paths that ultimately trace back to GitHub API data:
- `GitStore.addUpstreamRemoteIfNeeded()` / `ensureUpstreamRemoteURL()` use `parent.cloneURL`, sourced from the GitHub API's repository object [4](#0-3) .
- `AppStore._findPullRequestBranch()` uses `headCloneUrl` supplied for a pull request's head repository (the fork), calling `addRemote(repository, forkRemoteName, headCloneUrl)` when no matching remote already exists [5](#0-4) .

`urlMatchesRemote()` only checks hostname/owner/repo-name structure for known-remote comparisons [6](#0-5) ; it does not validate that the raw string is safe to pass as a positional git CLI argument, and `addRemote()` itself performs no such check either.

### Impact Explanation
In current call sites the observed URLs are always API-generated and prefixed with a scheme (`https://github.com/...`), which incidentally prevents the string from beginning with `-`. That means, based on the evidence available in this index, I could **not** confirm a fully attacker-controlled string reaching `addRemote()` unmodified/unprefixed. If such a path exists (e.g. a future or existing caller that passes a raw, non-prefixed value derived from PR/fork metadata, config import, or a different deep-link/API field), the missing `--` separator would allow argument injection into `git remote add`, potentially setting attacker-chosen git options that get persisted into `.git/config` and consumed on subsequent fetch/push operations — a much more severe outcome than the accidental trade-slippage class in the report, since it corrupts the git configuration that governs future remote operations.

### Likelihood Explanation
Low-to-uncertain today because the two identified callers happen to only ever pass API-shaped URLs (`https://...`) that can't start with `-`. This is a defense-in-depth gap rather than a demonstrated end-to-end exploit: I was not able to locate a call site in the available index where the raw, attacker-supplied value (e.g., the `open-repository-from-url` deep-link `url` field parsed in [7](#0-6) ) is passed to `addRemote()` without the scheme guaranteed. This is an important caveat: unlike `push.ts` (which is guarded by `--force-with-lease` plus a confirmation dialog, see `WarnForcePushDialog`) and `clone.ts` (guarded by the `--` separator and `isClonePathSensitive`), `addRemote()` has no equivalent hardening, so the invariant "URL strings are never treated as CLI flags" is enforced inconsistently across the git wrapper layer.

### Recommendation
Add the same `--` end-of-options separator used in `clone.ts` to `addRemote()` (and any other `git remote`/`git fetch`/`git push` call sites that accept externally-derived URL strings) so that a value beginning with `-` can never be parsed as a git option, regardless of which caller eventually supplies it.

### Proof of Concept
Not confirmed against a live attacker-controlled input in this codebase snapshot — the code-level gap is directly verifiable (compare `clone.ts` line 123 `args.push('--', url, path)` vs. `remote.ts` line 34 `['remote', 'add', name, url]`), but I could not trace a concrete unauthenticated trigger where `url` is both attacker-controlled and lacks a forced `https://`/`ssh://` prefix. Given this, I'm flagging it as a hardening gap discovered via local code evidence rather than a fully proven exploit chain; a Devin session with full file/history access could confirm whether any caller (present or historical) passes unprefixed, attacker-influenced strings to `addRemote()`.

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

**File:** app/src/lib/git/clone.ts (L119-123)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)
```

**File:** app/src/lib/stores/git-store.ts (L1317-1356)
```typescript
  /**
   * Add the upstream remote if the repository is a fork and an upstream remote
   * doesn't already exist.
   */
  public async addUpstreamRemoteIfNeeded(): Promise<void> {
    const parent =
      this.repository.gitHubRepository &&
      this.repository.gitHubRepository.parent
    if (!parent) {
      return
    }

    const remotes = await getRemotes(this.repository)
    const upstream = findUpstreamRemote(parent, remotes)
    if (upstream) {
      return
    }

    const remoteWithUpstreamName = remotes.find(
      r => r.name === UpstreamRemoteName
    )
    if (remoteWithUpstreamName) {
      const error = new UpstreamAlreadyExistsError(
        this.repository,
        remoteWithUpstreamName
      )
      this.emitError(error)
      return
    }

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

**File:** app/src/lib/stores/app-store.ts (L8647-8660)
```typescript
    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
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

**File:** app/src/lib/parse-app-url.ts (L66-124)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
```
