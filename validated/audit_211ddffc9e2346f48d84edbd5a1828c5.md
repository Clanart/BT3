### Title
GitHub identity keyed by mutable owner login + repo name allows silent hijack of a local repository's associated GitHubRepository record (repo/owner renaming & username squatting) - (File: app/src/lib/stores/repositories-store.ts)

### Summary
GoGoPool's bug was that a resource identified by a supposedly stable key (`nodeID`) could be re-associated with a new owner because `createMinipool`'s existing-record lookup and overwrite logic used the raw key instead of verifying the caller was the original owner. The analogous invariant in GitHub Desktop is: the `GitHubRepository` record that Desktop uses to decide clone/push/pull URLs and permissions for a locally-tracked repository must always refer to the *same* remote repository it was originally associated with. Desktop breaks this invariant because `RepositoriesStore` looks up and overwrites `GitHubRepository` rows using the mutable composite key `[ownerID + name]` (owner login string + repo name string) rather than GitHub's immutable numeric repository id. [1](#0-0) 

### Finding Description
`_upsertGitHubRepository` resolves whether an API repository object refers to an already-known local record purely via:

```
const existingRepo = await this.db.gitHubRepositories
  .where('[ownerID+name]')
  .equals([owner.id, gitHubRepository.name])
  .first()
``` [1](#0-0) 

If a match is found, the function reuses the **same database id** (`existingRepo.id`) and overwrites `cloneURL`, `htmlURL`, `private`, `permissions`, `parentID`, etc. with whatever came back from the API for that owner/name pair:

```
const updatedGitHubRepo: IDatabaseGitHubRepository = {
  ...(existingRepo?.id !== undefined && { id: existingRepo.id }),
  ownerID: owner.id,
  name: gitHubRepository.name,
  private: gitHubRepository.private,
  htmlURL: gitHubRepository.html_url,
  cloneURL: gitHubRepository.clone_url,
  parentID,
  ...
}
``` [2](#0-1) 

The `IDatabaseGitHubRepository` schema itself never stores GitHub's immutable numeric repository id at all — only `name`, `ownerID`, `htmlURL`, `cloneURL`, etc. — so there is no way for Desktop to distinguish "the same repo, updated" from "a completely different repo that now happens to occupy this owner+name slot." [3](#0-2) 

GitHub allows both repository renames and, more importantly, **username/organization renames**, which frees the old login string for anyone to register. Desktop's `owners` table keys an owner strictly by login+endpoint via `getOwnerKey`, and `putOwner`/`_upsertGitHubRepository` will happily bind a freshly-fetched owner object with that reused login to the pre-existing `ownerID` row, then match/overwrite the pre-existing `[ownerID+name]` `GitHubRepository` row for any repo name that was also reused. This is invoked automatically and silently during background refresh: `repositoryWithRefreshedGitHubRepository` in `app-store.ts` matches the local repo's git remote to an account/owner/name via `matchGitHubRepository`, fetches `api.fetchRepository(owner, name)`, and calls `repoStore.upsertGitHubRepository(endpoint, apiRepo)` unconditionally, without ever checking that the returned repository object refers to the same underlying GitHub repo id as previously stored. [4](#0-3) 

This is the direct structural analog of the GoGoPool bug: the "overwrite if key already exists" path in `createMinipool`/`requireValidStateTransition` had no check that the caller was the original owner of `nodeID`; here, the "overwrite if `[ownerID+name]` already exists" path in `_upsertGitHubRepository` has no check that the API repository is the same underlying repository (by immutable id) as the one previously stored under that key.

### Impact Explanation
When the `GitHubRepository` record backing a user's local repository is silently repointed to an attacker-controlled repository (via login/name squatting after a rename), `updateRemoteUrl` (called from `repositoryWithRefreshedGitHubRepository` right before the upsert) rewrites the local git remote's URL to the new `cloneURL`, and downstream flows (branch protection checks, "View on GitHub", PR fetch/push target resolution, and permission checks used to gate force-push/protected-branch warnings) all key off this same in-memory `GitHubRepository` object. This can cause pushes/pulls to be silently redirected to an attacker's repository (data exfiltration of code the user pushes) or cause protection/permission state (e.g., `permissions`, `isArchived`, branch protection cache keyed by `dbID`) to be taken from the attacker's repo and misapplied to the user's local workflow — matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Exploitation requires no local access, malware, or leaked credentials — only that a GitHub owner login (or repo name under a login) becomes available for re-registration (a well-documented "repo-jacking"/username-squatting scenario) and that a victim's Desktop client refreshes its GitHub repository association in the background (`repositoryWithRefreshedGitHubRepository`), which happens automatically and periodically, not on any unusual user action. The attacker's only requirement is registering the freed login/name and creating a repository — actions fully under attacker control via the ordinary GitHub API/UI, consistent with the "attacker controls a GitHub API object" trigger condition.

### Recommendation
Store and match on GitHub's immutable numeric repository id (`IAPIRepository.id`) rather than (or in addition to, as a required equality check) `[ownerID+name]`. `_upsertGitHubRepository` should treat a match found by `[ownerID+name]` as authoritative only if the fetched API repository's immutable id also matches the previously stored id; if it differs, a *new* `GitHubRepository` record should be created instead of overwriting the existing one, analogous to adding an `onlyOwner`-style identity check before allowing the record to be reused/overwritten.

### Proof of Concept
Conceptual reproduction based on the code paths above (not independently executed, since this requires live GitHub state changes such as account renames):
1. User adds local repo cloned from `https://github.com/victim/repo`. Desktop calls `upsertGitHubRepository` and stores a `GitHubRepository` row keyed by `ownerID(victim)+"repo"` with `cloneURL=https://github.com/victim/repo.git`.
2. `victim` renames their GitHub account to `victim2` (freeing the `victim` login) but keeps using Desktop; alternatively `victim` deletes/renames the repo, freeing the `repo` name under that login.
3. An attacker registers the now-available login `victim` (or creates a repo named `repo` under the same still-existing `victim` login after deleting/recreating), pushes malicious content or sets a different `clone_url`.
4. On the next background refresh, `repositoryWithRefreshedGitHubRepository` (`app-store.ts:4874-4914`) calls `matchGitHubRepository`/`api.fetchRepository(owner, name)` and `repoStore.upsertGitHubRepository(endpoint, apiRepo)`.
5. `_upsertGitHubRepository`'s `[ownerID+name]` lookup (`repositories-store.ts:613-616`) matches the pre-existing DB row and overwrites its `cloneURL`/`htmlURL`/`permissions` fields with the attacker's repository data — and `updateRemoteUrl` rewrites the user's actual git remote to point at the attacker's repository, with no verification that this is the same underlying GitHub repository as before.

### Citations

**File:** app/src/lib/stores/repositories-store.ts (L613-616)
```typescript
    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()
```

**File:** app/src/lib/stores/repositories-store.ts (L654-666)
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
```

**File:** app/src/lib/databases/repositories-database.ts (L19-36)
```typescript
export interface IDatabaseGitHubRepository {
  readonly id?: number
  readonly ownerID: number
  readonly name: string
  readonly private: boolean | null
  readonly htmlURL: string | null
  readonly cloneURL: string | null

  /** The database ID of the parent repository if the repository is a fork. */
  readonly parentID: number | null
  /** The last time a prune was attempted on the repository */
  readonly lastPruneDate: number | null

  readonly issuesEnabled?: boolean
  readonly isArchived?: boolean

  readonly permissions?: 'read' | 'write' | 'admin' | null
}
```

**File:** app/src/lib/stores/app-store.ts (L4904-4910)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)
```
