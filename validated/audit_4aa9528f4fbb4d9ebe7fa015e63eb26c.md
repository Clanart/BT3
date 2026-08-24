## Finding

### Title
Unsanitized fork parent `cloneURL` passed to `git remote add` enables `ext::` transport command execution - (File: app/src/lib/stores/git-store.ts)

### Summary
`GitStore.addUpstreamRemoteIfNeeded` takes the `parent.cloneURL` value of a `GitHubRepository` object — populated from a GitHub API response's `clone_url`/parent field — and passes it, unvalidated, directly into `addRemote()`, which spawns `git remote add upstream <url>` as a literal argv element.

### Finding Description
In `app/src/lib/stores/git-store.ts:1347-1355`, the upstream URL is only unwrapped, never validated: [1](#0-0) 

It's forwarded to `addRemote`: [2](#0-1) 

The same unsanitized pattern exists in `updateExistingUpstreamRemote`: [3](#0-2) 

The `cloneURL` field ultimately originates from a `GitHubRepository`/`IAPIRepository` object built from an API response (`clone_url`), as seen in `repositories-store.ts`'s reconstruction of the parent chain and `api-repositories-store.ts`'s use of `IAPIRepository.clone_url`: [4](#0-3) [5](#0-4) 

I found no scheme allow-list, no `--`-prefix rejection, and no `GIT_ALLOW_PROTOCOL`/`protocol.*.allow` environment configuration anywhere in the git invocation stack (`core.ts`, `environment.ts`) that would block dangerous git transports such as `ext::` or reject option-like (`-`/`--` prefixed) remote URLs before they reach `git remote add`: [6](#0-5) 

Because argv is passed as an array (not through a shell), classic shell-metacharacter/`$IFS` injection is not directly exploitable here; however, git's own `ext::` remote helper transport spawns `/bin/sh -c` with the remainder of the URL when the remote is later used (e.g., on fetch/push of the newly added `upstream` remote), independent of how Desktop invokes git.

### Impact Explanation
If a malicious or compromised GitHub API endpoint (e.g., a rogue/compromised GitHub Enterprise Server the user has added as an account, or a tampered API response) returns a parent repository object with `clone_url` set to `ext::sh -c '<attacker command>'`, that string is written verbatim as a git remote URL. Any subsequent git operation that touches the `upstream` remote (fetch, pull, prune, background fetcher) will cause git to execute the attacker-supplied shell command via the `ext::` helper, achieving arbitrary code execution.

### Likelihood Explanation
Exploitation requires the attacker to control the GitHub API's response for the parent repository object of a fork — this is realistic in the threat model of a malicious/compromised GitHub Enterprise Server endpoint or a MITM'd/rogue API response, which is within the stated scope ("API objects", "remote/proxy response"). It's not exploitable against the real github.com API, since GitHub server-side generates `clone_url` from the real owner/repo path rather than accepting arbitrary attacker text — so the realistic path is a non-github.com endpoint under attacker control.

### Recommendation
Before using any GitHub API-supplied `cloneURL` (or any remote URL derived from an untrusted source) as a git remote URL, validate that:
1. It does not begin with `-` (to prevent option/flag injection into `git remote add`), and
2. Its scheme is restricted to a known-safe allow-list (`https`, `http`, `git`, `ssh`), explicitly rejecting `ext::`, `fd::`, and other command-executing git transports.

Alternatively/additionally, set `GIT_ALLOW_PROTOCOL` (or pass `-c protocol.ext.allow=never -c protocol.fd.allow=never`) in the environment used for all git invocations in `app/src/lib/git/core.ts`/`environment.ts` to categorically disable dangerous transports regardless of source.

### Proof of Concept
A focused unit test on `GitStore.addUpstreamRemoteIfNeeded` (or a lower-level test of `addRemote`) with a `GitHubRepository` parent whose `cloneURL` is `'ext::sh -c touch$IFS/tmp/pwned'` should assert that the resulting `git remote add upstream <url>` call is rejected/sanitized. As currently implemented, no such check exists in `git-store.ts:1347-1355`, `remote.ts:29-37`, `core.ts`, or `environment.ts`, so the malicious value would be passed through unmodified to git.

Note: I was unable to fully verify the currently-configured system/bundled git version's default `protocol.ext.allow` policy in this codebase (it is a git binary default, not something set by Desktop), so exploitability also depends on the git version bundled with dugite; this should be confirmed by a Devin session with access to run the actual bundled git binary.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1347-1355)
```typescript
    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
```

**File:** app/src/lib/stores/git-store.ts (L1683-1694)
```typescript
    const parent = forceUnwrap(
      'To update an upstream remote, the repository must have a parent',
      gitHubRepository.parent
    )
    const url = forceUnwrap(
      'Parent repositories are always fully loaded',
      parent.cloneURL
    )

    await this.performFailableOperation(() =>
      setRemoteURL(this.repository, UpstreamRemoteName, url)
    )
```

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

**File:** app/src/lib/stores/repositories-store.ts (L126-137)
```typescript
    const ghRepo = new GitHubRepository(
      repo.name,
      owner,
      repo.id,
      repo.private,
      repo.htmlURL,
      repo.cloneURL,
      repo.issuesEnabled,
      repo.isArchived,
      repo.permissions,
      parent
    )
```

**File:** app/src/lib/stores/api-repositories-store.ts (L182-195)
```typescript
    const missing = new Map<string, IAPIRepository>()
    const repositories = new Map<string, IAPIRepository>()

    currentState?.repositories.forEach(r => {
      missing.set(r.clone_url, r)
      repositories.set(r.clone_url, r)
    })

    const addPage = (page: ReadonlyArray<IAPIRepository>) => {
      page.forEach(r => {
        repositories.set(r.clone_url, r)
        missing.delete(r.clone_url)
      })
      this.updateAccount(account, { repositories: [...repositories.values()] })
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
