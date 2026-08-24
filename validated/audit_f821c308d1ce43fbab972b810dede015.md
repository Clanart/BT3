### Title
Delimiter-based `createEqualityHash` allows repository-identity collisions across `GitStoreCache` / `RepositoryStateCache` - (File: `app/src/models/equality-hash.ts`)

### Summary
`createEqualityHash()` builds identity hashes for `Repository` and `GitHubRepository` by naively joining their fields with the literal character `'+'`, with no escaping and no length-prefixing. Several of the joined fields (`name`, `htmlURL`, `cloneURL`, `permissions`, etc.) are populated directly from GitHub/GHE API responses or from the parsed remote. Because `Array.prototype.join('+')` collapses field boundaries, two structurally different `Repository`/`GitHubRepository` objects can be crafted (by controlling one API-response field to itself contain a `+`) so that they produce the *same* `hash` string — exactly the `abi.encodePacked` collision pattern described in the source report, just applied to string concatenation for object identity instead of a CREATE2 salt.

### Finding Description
`createEqualityHash` is implemented as: [1](#0-0) 

It is used to compute `GitHubRepository.hash` from `name, owner.login, dbID, isPrivate, htmlURL, cloneURL, issuesEnabled, isArchived, permissions, parent?.hash`: [2](#0-1) 

and to compute `Repository.hash` from `path, id, gitHubRepository?.hash, missing, alias, workflowPreferences.forkContributionTarget, isTutorialRepository`: [3](#0-2) 

Because `join('+')` (like `abi.encodePacked`) has no delimiter escaping, shifting a `+` character from one field into an adjacent field produces an identical joined string. For example:
```
["repo", "alice.git+extra"].join('+') === "repo+alice.git+extra"
["repo+alice.git", "extra"].join('+') === "repo+alice.git+extra"
```
`name`, `htmlURL`, and `cloneURL` on `GitHubRepository` are populated straight from API JSON returned by the configured endpoint (`api.github.com` or a self-hosted GitHub Enterprise Server), so a malicious/compromised GHE instance or a MITM proxy sitting on an enterprise git remote can choose these strings, including embedding `+` characters, to steer the resulting joined hash to collide with the hash of a different, legitimate repository the user already has open in Desktop.

No guard exists anywhere in `createEqualityHash`, `GitHubRepository`, or `Repository` to reject or escape the delimiter character before joining, so nothing stops this collision once an attacker controls any one of the joined string fields.

### Impact Explanation
`Repository.hash` (not `id`) is the sole cache key for at least two critical, per-repository singletons:

- `GitStoreCache`, which holds the `GitStore` (branch/ref/HEAD/commit state) per repository, keyed only by `repository.hash`: [4](#0-3) 

- `RepositoryStateCache`, which holds UI/branches/changes state per repository, also keyed only by `repository.hash`: [5](#0-4) 

If two distinct `Repository` objects collide on `.hash`, `GitStoreCache.get()` will hand back the *same* `GitStore` instance for both repositories, and `RepositoryStateCache.get()` will hand back the same cached branch/changes/tip state for both. This means Desktop can silently commit, branch off, or push using git state (current tip, ahead/behind, working directory changes) that actually belongs to a different repository than the one the user believes is selected — i.e., silent corruption of what the user commits or pushes, which is explicitly listed as valid impact.

### Likelihood Explanation
Exploitation requires the attacker to control content returned for a `GitHubRepository`'s `name`, `htmlURL`, or `cloneURL` field — achievable by operating or man-in-the-middling a GitHub Enterprise Server endpoint that the victim has added as an account, or by controlling a repository's metadata surfaced through the API that Desktop consumes when cloning/matching remotes (`app/src/lib/remote-parsing.ts`, `app/src/lib/repository-matching.ts`). This fits the allowed threat model ("attacker controls…a GitHub API object…or a git remote/proxy response"). Constructing an exact-length collision requires the attacker to know or brute-force the victim's existing repository's field values (path length is local and less predictable, but `GitHubRepository` fields such as `name`/`owner.login`/`cloneURL` are often public), which lowers but does not eliminate practical likelihood — it is a real but constrained collision surface, consistent with the "Medium" severity of the original CREATE2 report.

### Recommendation
Replace the ad-hoc delimiter join in `createEqualityHash` (`app/src/models/equality-hash.ts`) with a collision-resistant, length-prefixed or JSON-based encoding (e.g., `JSON.stringify` of a tuple, or hashing each field's length + value before concatenation) so that no combination of field values can produce the same serialized output as another. This is the direct analog of the report's recommendation to replace `abi.encodePacked()` with `abi.encode()`.

### Proof of Concept
1. Victim adds `RepoA` from a GitHub Enterprise Server, resulting in a `GitHubRepository` with `cloneURL = "https://ghe.local/alice/repo.git+extra"` and other fields `F`.
2. An attacker-controlled/compromised GHE instance (or MITM proxy) serves repository metadata for a different repository `RepoB` whose `cloneURL = "https://ghe.local/alice/repo.git"` and whose next joined field is crafted to start with `"extra"` followed by the rest of `F`, such that:
```
[name, owner, dbID, isPrivate, htmlURL, cloneURLA, issuesEnabled, ...].join('+')
  === [name, owner, dbID, isPrivate, htmlURL, cloneURLB, "extra"+issuesEnabled, ...].join('+')
```
3. Both `GitHubRepository` (and therefore wrapping `Repository`) objects now produce an identical `.hash`.
4. `GitStoreCache.get(repoB)` returns the already-cached `GitStore` created for `repoA`, so branch/HEAD/working-directory state from `repoA` is used while the UI/user context shows `repoB`.
5. The user proceeds to commit/push from what they believe is `repoB`, but the underlying `GitStore` operates on `repoA`'s cached tip/branch data, silently corrupting the commit/push target.

### Citations

**File:** app/src/models/equality-hash.ts (L15-17)
```typescript
export function createEqualityHash(...items: HashableType[]) {
  return items.join('+')
}
```

**File:** app/src/models/github-repository.ts (L32-43)
```typescript
    this.hash = createEqualityHash(
      this.name,
      this.owner.login,
      this.dbID,
      this.isPrivate,
      this.htmlURL,
      this.cloneURL,
      this.issuesEnabled,
      this.isArchived,
      this.permissions,
      this.parent?.hash
    )
```

**File:** app/src/models/repository.ts (L72-80)
```typescript
    this.hash = createEqualityHash(
      path,
      this.id,
      gitHubRepository?.hash,
      this.missing,
      this.alias,
      this.workflowPreferences.forkContributionTarget,
      this.isTutorialRepository
    )
```

**File:** app/src/lib/stores/git-store-cache.ts (L20-37)
```typescript
  public remove(repository: Repository) {
    if (this.gitStores.has(repository.hash)) {
      this.gitStores.delete(repository.hash)
    }
  }

  public get(repository: Repository): GitStore {
    let gitStore = this.gitStores.get(repository.hash)
    if (gitStore === undefined) {
      gitStore = new GitStore(repository, this.shell, this.statsStore)
      gitStore.onDidUpdate(() => this.onGitStoreUpdated(repository, gitStore!))
      gitStore.onDidError(error => this.onDidError(error))

      this.gitStores.set(repository.hash, gitStore)
    }

    return gitStore
  }
```

**File:** app/src/lib/stores/repository-state-cache.ts (L36-45)
```typescript
  public get(repository: Repository): IRepositoryState {
    const existing = this.repositoryState.get(repository.hash)
    if (existing != null) {
      return existing
    }

    const newItem = getInitialRepositoryState()
    this.repositoryState.set(repository.hash, newItem)
    return newItem
  }
```
