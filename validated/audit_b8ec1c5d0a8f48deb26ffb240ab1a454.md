Based on my research, I found a strong structural analog. Note: I was unable to fully verify `putOwner()`'s exact matching logic or the `GitHubRepository.hash` getter contents due to tool call limits reached mid-investigation — the finding below is grounded in what I confirmed directly.

### Title
Repository identity keyed on mutable `[ownerID+name]` instead of GitHub's immutable repository id lets attacker-supplied fork metadata silently overwrite a tracked repository's stored identity - (File: `app/src/lib/stores/repositories-store.ts`)

### Summary
`RepositoriesStore._upsertGitHubRepository()` resolves and updates the persisted `GitHubRepository` record by looking it up on the composite key `[ownerID+name]` — i.e., the owner's login string plus the repository name string — rather than GitHub's immutable numeric repository id, even though that id is present on every `IAPIRepository`/`IAPIFullRepository` object Desktop receives from the API.

### Finding Description
The lookup that decides whether an incoming API repository object refers to an *existing* local record is: [1](#0-0) 

and the analogous "from match" path uses the same mutable pair: [2](#0-1) 

Once a match is found, the code unconditionally overwrites the stored `cloneURL`, `parentID`, `htmlURL`, `permissions`, and archived/issues flags for that database row: [3](#0-2) 

This path is reachable from attacker-influenced data: when Desktop fetches pull requests, `pr.head.repo` (the fork the PR came from) and `pr.base.repo` are arbitrary `IAPIRepository` JSON fields taken straight from the API/GHE server response, and each is upserted via `store.upsertGitHubRepositoryLight`: [4](#0-3) [5](#0-4) [6](#0-5) 

Just as `ExtraordinaryFunding.proposeExtraordinary()` hashed everything except `endBlock_` (the field that actually determines proposal validity/lifetime), `_upsertGitHubRepository` keys repository identity on everything except the one field that GitHub guarantees is unique and immutable — the repository's numeric `id`. Owner login and repo name are both mutable (renames, org transfers, deletions+recreation, or username-squatting after a rename) and, for a fork, the `name`/`owner.login` pair is fully attacker-chosen data embedded inside an API object that legitimately reaches Desktop through routine PR syncing.

### Impact Explanation
If an attacker can cause a repository object whose `[ownerID+name]` collides with an existing tracked repository's key to flow through this path (e.g., by exploiting GitHub's username-reuse window after a rename, or by controlling a compromised/malicious GHE server acting as the API endpoint — an explicitly in-scope "git remote/proxy response" attacker), the existing `GitHubRepository` row's `cloneURL`/`parentID`/`permissions` are silently replaced with the attacker's values. Downstream consumers (fork detection, `_convertRepositoryToFork`, branch-protection lookups keyed by `dbID`) then operate on the corrupted identity, which can misdirect where the user believes they are pushing/pulling from — a silent corruption of the repository's trust metadata, structurally the same "wrong entity accepted under a colliding key" failure as the Ajna bug.

### Likelihood Explanation
Lower than the original contract bug because it requires either (a) a real GitHub username-rename race/squat, or (b) control of a GitHub Enterprise Server / proxy endpoint the client trusts — both narrower preconditions than plain mempool frontrunning. I was not able to fully trace `putOwner()`'s case-insensitive login matching (`getOwnerKey`) in this session to confirm exactly how quickly a squatted login would collide, which is a gap in this analysis: [7](#0-6) 

### Recommendation
Include GitHub's immutable numeric repository `id` (and ideally the owner's immutable numeric id, not just login) in the key used to find/update `GitHubRepository` records, falling back to `[ownerID+name]` only for legacy records without a stored numeric id, analogous to adding `endBlock_` back into the proposal hash in the original finding.

### Proof of Concept
Not independently executed in this session (no sandbox access). Conceptually: (1) attacker forks a repo under a username that a victim organization previously owned and renamed away from; (2) attacker creates/keeps a repository whose name matches a repo the victim already has tracked locally under the old owner; (3) attacker opens a PR against any repo the victim's Desktop client syncs, causing `pull-request-store.ts` to upsert `pr.head.repo` through `_upsertGitHubRepository`; (4) the `[ownerID+name]` match resolves to the victim's existing `GitHubRepository` row and overwrites its `cloneURL`/`parentID`/`permissions` with the attacker's fork data.

### Citations

**File:** app/src/lib/stores/repositories-store.ts (L540-543)
```typescript
        const existingRepo = await this.db.gitHubRepositories
          .where('[ownerID+name]')
          .equals([owner.id, match.name])
          .first()
```

**File:** app/src/lib/stores/repositories-store.ts (L613-616)
```typescript
    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()
```

**File:** app/src/lib/stores/repositories-store.ts (L654-679)
```typescript
    const updatedGitHubRepo: IDatabaseGitHubRepository = {
      ...(existingRepo?.id !== undefined && { id: existingRepo.id }),
      ownerID: owner.id,
      name: gitHubRepository.name,
      private: gitHubRepository.private,
      htmlURL: gitHubRepository.html_url,
      cloneURL: gitHubRepository.clone_url,
      parentID,
      lastPruneDate: existingRepo?.lastPruneDate ?? null,
      issuesEnabled: gitHubRepository.has_issues,
      isArchived: gitHubRepository.archived,
      permissions,
    }

    if (existingRepo !== undefined) {
      // If nothing has changed since the last time we persisted the API info
      // we can skip writing to the database and (more importantly) avoid
      // telling store consumers that the repo store has changed.
      if (shallowEquals(existingRepo, updatedGitHubRepo)) {
        return this.toGitHubRepository(existingRepo, owner, parent)
      }
    }

    const id = await this.db.gitHubRepositories.put(updatedGitHubRepo)
    this.emitUpdatedRepositories()
    return this.toGitHubRepository({ ...updatedGitHubRepo, id }, owner, parent)
```

**File:** app/src/lib/stores/pull-request-store.ts (L256-266)
```typescript
    const upsertRepo = mem(store.upsertGitHubRepositoryLight.bind(store), {
      // The first argument which we're ignoring here is the endpoint
      // which is constant throughout the lifetime of this function.
      // The second argument is an `IAPIRepository` which is basically
      // the raw object that we got from the API which could consist of
      // more than just the fields we've modelled in the interface. The
      // only thing we really care about to determine whether the
      // repository has already been inserted in the database is the clone
      // url since that's what the upsert method uses as its key.
      cacheKey: (_, repo) => repo.clone_url,
    })
```

**File:** app/src/lib/stores/pull-request-store.ts (L281-281)
```typescript
      const baseGitHubRepo = await upsertRepo(endpoint, pr.base.repo)
```

**File:** app/src/lib/stores/pull-request-store.ts (L303-303)
```typescript
      const headRepo = await upsertRepo(endpoint, pr.head.repo)
```

**File:** app/src/lib/databases/repositories-database.ts (L245-252)
```typescript
/* Creates a case-insensitive key used to uniquely identify an owner
 * based on the endpoint and login. Note that the key happens to
 * match the canonical API url for the user. This has no practical
 * purpose but can make debugging a little bit easier.
 */
export function getOwnerKey(endpoint: string, login: string) {
  return `${endpoint}/users/${login}`.toLowerCase()
}
```
