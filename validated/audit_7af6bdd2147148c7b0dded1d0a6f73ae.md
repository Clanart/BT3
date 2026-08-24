Based on my research, I was not able to fully verify the exact call-site logic in `dispatcher.ts`/`app-store.ts` that consumes `matchExistingRepository` before the tool budget ran out, so I present this with that caveat. The strongest analog I can support with concrete file/function evidence is below.

### Title
Repository identity resolved by filesystem path instead of remote URL, allowing a stale/incidental local folder to be silently treated as the target repo - (File: `app/src/lib/repository-matching.ts`)

### Summary
The external report's broken invariant is: a *derived, non-content-bound identifier* (proxy address from factory+nonce) is trusted as proof of "this is the target the user intended," and that identifier can shift out from under the user due to external reordering (a reorg), causing funds to flow to the wrong destination. The Desktop analog is `matchExistingRepository`, which decides "is this local folder the repository the user is trying to open/clone/interact with?" purely by comparing the *filesystem path*, never the remote URL or owner/name identity of the repository that actually lives there.

### Finding Description
`matchExistingRepository` in `app/src/lib/repository-matching.ts` is defined as: [1](#0-0) 

It normalizes and compares only `r.path === needle`; it never inspects `r.gitHubRepository`, the git remotes, or calls `repositoryMatchesRemote`/`urlMatchesRemote` (which do exist in the same file and are the "correct" identity check used elsewhere): [2](#0-1) 

This means "which tracked repository is this on-disk folder" is answered with a value (the path) that is incidental to the actual identity of the repository content — analogous to how the DAO report's `daoMembershipAddress` (derived from factory+nonce) is incidental to which DAO configuration it actually points to. Just as a Polygon reorg can swap which nonce lands first and silently repoint an address to a different DAO, any situation where a path ends up occupied by unrelated repository content (previous clone to the same default directory, a directory reused after deletion, a symlink, or a race in clone-target selection) can silently repoint Desktop's notion of "the repository" to the wrong git history/remote, without any remote-identity check to catch the mismatch.

This function is consumed in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/stores/app-store.ts` (5 and 3 references respectively) and `app/src/ui/app.tsx`, which I confirmed exist but did not have the remaining tool budget to fully trace line-by-line to see whether any of those call sites add a secondary remote-URL cross-check before treating the path match as authoritative.

### Impact Explanation
If a deep link ("Open in Desktop") or clone flow resolves the destination folder to a path that Desktop's tracked-repository list already matches for unrelated content, the app could present, commit to, or push against the wrong repository's history while the UI shows the name/URL the user expected — a silent corruption of what the user commits/pushes, which is explicitly listed as valid impact.

### Likelihood Explanation
Uncertain/Medium. I confirmed the path-only matching primitive as a real, unguarded code fact, but I was not able to verify within this session whether every call site of `matchExistingRepository` is followed by a remote-URL/GitHub-repo identity check (which would neutralize the issue) — the search results only show reference counts, not the surrounding logic at each call site. This should be treated as a lead requiring call-site verification (in `dispatcher.ts`, `app-store.ts`, `app.tsx`) rather than a fully proven end-to-end exploit chain.

### Recommendation
At every call site that uses `matchExistingRepository` to decide whether an incoming "open repository" action (deep link, clone-to-existing-folder, CLI open) should reuse a tracked repository, require the match to be confirmed by `repositoryMatchesRemote`/`urlMatchesRemote` against the actual git remote of the folder before treating it as the same repository, not just a path-string equality check.

### Proof of Concept
Not independently executed in this session — this reflects a static-analysis finding based on `app/src/lib/repository-matching.ts:54-65` compared against the identity-verification helpers in the same file (`app/src/lib/repository-matching.ts:73-118`), which are not used by `matchExistingRepository`. Full confirmation would require tracing each of the 8 call sites in `dispatcher.ts`/`app-store.ts`/`app.tsx` to see whether a remote check is layered on top before the match result is acted upon.

### Citations

**File:** app/src/lib/repository-matching.ts (L54-65)
```typescript
export function matchExistingRepository<T extends { readonly path: string }>(
  repos: ReadonlyArray<T>,
  path: string
): T | undefined {
  // Windows is guaranteed to be case-insensitive so we can be a bit less strict
  const normalize = __WIN32__
    ? (p: string) => Path.normalize(p).toLowerCase()
    : (p: string) => Path.normalize(p)

  const needle = normalize(path)
  return repos.find(r => normalize(r.path) === needle)
}
```

**File:** app/src/lib/repository-matching.ts (L73-118)
```typescript
export function repositoryMatchesRemote(
  gitHubRepository: GitHubRepository,
  remote: IRemote
): boolean {
  return (
    urlMatchesRemote(gitHubRepository.htmlURL, remote) ||
    urlMatchesRemote(gitHubRepository.cloneURL, remote)
  )
}

/**
 * Check whether or not a GitHub repository URL matches a given remote, by
 * parsing and comparing the structure of the each URL.
 *
 * @param url a URL associated with the GitHub repository
 * @param remote the remote details found in the Git repository
 */
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
