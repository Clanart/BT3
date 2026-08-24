## Analysis

Local evidence shows a broken-invariant pattern structurally identical to the reported bug class: **trust/identity state is cached and matched using a mutable, attacker-reclaimable string key instead of GitHub's immutable numeric identifiers**, so an entity ("the DAppControl at that address" ⇔ "the GitHub owner/repo at that login+name") can be silently swapped out while Desktop's local trust record continues to treat it as the same, already-trusted entity.

### The mechanism

`Owner` rows are looked up purely by a case-insensitive `endpoint+login` string key, never by GitHub's immutable numeric owner id: [1](#0-0) [2](#0-1) 

`GitHubRepository` rows are then looked up/reused by `[ownerID+name]` — again a mutable, reusable pair of a login-derived id and a plain repo-name string, not GitHub's numeric repository id: [3](#0-2) 

When an existing row is found for that `[ownerID+name]` key, `_upsertGitHubRepository` overwrites `htmlURL`, `cloneURL`, `parentID`, `permissions`, etc. **while keeping the same local `dbID`**: [4](#0-3) 

That `dbID` is the durable key that every other trust/permission cache in the app is built on: pull requests [5](#0-4) , mentionable users [6](#0-5) , branch-protection settings and last-prune date [7](#0-6) , and the write-permission gate [8](#0-7) .

GitHub allows both repository-name reuse (delete + recreate) and login reuse (an org/user renames, freeing the old login for anyone to claim). Neither event changes the local `[ownerID+name]` key, so this is a real, attacker-reachable "API object" divergence, exactly analogous to the report's "control address stays the same but the underlying behavior/config changes."

### Title
Owner/repository identity cached by mutable `login`+`name` instead of GitHub's immutable numeric IDs allows silent repository/owner takeover in local trust store - (File: `app/src/lib/stores/repositories-store.ts`, `app/src/lib/databases/repositories-database.ts`)

### Summary
Desktop's local repository database keys `Owner` rows on `endpoint+login` and `GitHubRepository` rows on `[ownerID+name]`, both of which are mutable, GitHub-reusable strings, rather than on GitHub's immutable numeric account/repository IDs. Because the local `dbID` (used by every downstream cache — PRs, mentionable users, branch protection, permissions) is preserved across an upsert that only checks `[ownerID+name]`, a GitHub login or repository name that becomes available again (via rename or delete) and is reclaimed by an attacker will be silently merged into the user's existing, previously-trusted local repository record.

### Finding Description
`getOwnerKey` computes owner identity solely from `endpoint` + `login` (case-insensitively), never from GitHub's immutable numeric owner id [1](#0-0) . `_upsertGitHubRepository` resolves the owner via this key and then looks up the existing `GitHubRepository` row by `[ownerID+name]` [9](#0-8) . If a row is found, its `htmlURL`, `cloneURL`, `parentID`, and `permissions` fields are overwritten with the freshly fetched API data while the row's primary key (`dbID`) is preserved [4](#0-3) .

No code path checks that the numeric `owner.id` or repository `id` returned by the GitHub API for that login/name pair still matches what was previously observed — the merge is keyed entirely on the reusable strings. Because `dbID` is unchanged, every table that trusts `dbID` as a stable proxy for "this specific GitHub repository" (pull requests, mentionable users, branch protection cache, prune-date cache, permission-derived `hasWritePermission`) will continue serving/storing data under that same key even after the underlying remote entity has been replaced.

### Impact Explanation
If a tracked organization/user login is renamed and later reclaimed by an attacker (or a repository is deleted and an attacker recreates one with the same name under the same still-controlled/reclaimed login), the next background repository/API refresh will silently rebind the user's local, already-trusted `GitHubRepository` record to the attacker's repository without changing the on-disk git remote. Consequences include: the attacker's collaborators being surfaced as "mentionable users" for autocompletion, the attacker's permission level silently governing `hasWritePermission`-gated UI, and the attacker's branch-protection state (or lack thereof) suppressing warnings Desktop would otherwise show before a push — i.e., corruption of the trust context under which the user commits/pushes, without any signal that the underlying repository identity has changed.

### Likelihood Explanation
Requires a specific but realistic sequence: a login/org rename or repository deletion that frees a login/name pair, an attacker claiming it and creating a same-named repository, and Desktop performing a background refresh (`upsertGitHubRepositoryLight`/`upsertGitHubRepository`) for that owner/name while the victim still has the original local repository tracked. This is fully attacker/GitHub-API-object driven and needs no local access, admin rights, or social engineering beyond normal GitHub account/repo lifecycle actions — but it does depend on the victim's original login/name becoming available, which is not always guaranteed to happen or be noticed by the attacker in time. Overall likelihood is low-to-medium.

### Recommendation
Key `Owner` and `GitHubRepository` identity on GitHub's immutable numeric IDs (`owner.id`/`repository.id` from the API) rather than on `login`/`name` strings. If the numeric ID returned for a previously-known `[endpoint+login]`/`[ownerID+name]` combination differs from the one on record, treat it as a new, distinct entity (new `dbID`) instead of merging into the existing row, and surface this discrepancy to the user rather than silently overwriting cached permissions/parent/clone data.

### Proof of Concept
1. User tracks `https://github.com/victim-org/project` in Desktop; local DB stores `Owner{endpoint, login:'victim-org'}` and `GitHubRepository{ownerID, name:'project', dbID:7, permissions:'write'}`.
2. `victim-org` renames itself, freeing the `victim-org` login on GitHub.
3. Attacker registers `victim-org` and creates a repository named `project`.
4. On the next background API refresh for that endpoint/login/name, `_upsertGitHubRepository` matches the existing owner row via `getOwnerKey` and the existing repo row via `[ownerID+name]`, then overwrites `cloneURL`, `parentID`, and `permissions` with the attacker's data while keeping `dbID: 7` [4](#0-3) .
5. All subsequent lookups keyed by `dbID` (mentionable users, PR list, branch protection, write-permission checks) now reflect the attacker-controlled repository under the identity the user previously trusted.

### Citations

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

**File:** app/src/models/owner.ts (L1-13)
```typescript
import { GitHubAccountType } from '../lib/api'

/** The owner of a GitHubRepository. */
export class Owner {
  /**
   * @param id The database ID. This may be null if the object wasn't retrieved from the database.
   */
  public constructor(
    public readonly login: string,
    public readonly endpoint: string,
    public readonly id: number,
    public readonly type?: GitHubAccountType
  ) {}
```

**File:** app/src/lib/stores/repositories-store.ts (L44-53)
```typescript
   * Key is the GitHubRepository id, value is the protected branch count reported
   * by the GitHub API.
   */
  private branchProtectionSettingsFoundCache = new Map<number, boolean>()

  /**
   * Key is the lookup by the GitHubRepository id and branch name, value is the
   * flag whether this branch is considered protected by the GitHub API
   */
  private protectionEnabledForBranchCache = new Map<string, boolean>()
```

**File:** app/src/lib/stores/repositories-store.ts (L596-616)
```typescript
  private async _upsertGitHubRepository(
    endpoint: string,
    gitHubRepository: IAPIRepository | IAPIFullRepository,
    ignoreParent = false
  ): Promise<GitHubRepository> {
    const parent =
      'parent' in gitHubRepository && gitHubRepository.parent !== undefined
        ? await this._upsertGitHubRepository(
            endpoint,
            gitHubRepository.parent,
            true
          )
        : await Promise.resolve(null) // Dexie gets confused if we return null

    const { login, type } = gitHubRepository.owner
    const owner = await this.putOwner(endpoint, login, type)

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

**File:** app/src/lib/databases/pull-request-database.ts (L191-203)
```typescript
  public getAllPullRequestsInRepository(repository: GitHubRepository) {
    return this.pullRequests
      .where('[base.repoId+number]')
      .between([repository.dbID], [repository.dbID + 1])
      .toArray()
  }

  /**
   * Get a single pull requests for a particular repository
   */
  public getPullRequest(repository: GitHubRepository, prNumber: number) {
    return this.pullRequests.get([repository.dbID, prNumber])
  }
```

**File:** app/src/lib/databases/github-user-database.ts (L32-39)
```typescript
interface IDBMentionableUser extends IMentionableUser {
  /**
   * The id corresponding to the dbID property of the
   * `GitHubRepository` instance that this user is associated
   * with
   */
  readonly gitHubRepositoryID: number
}
```

**File:** app/src/models/github-repository.ts (L77-84)
```typescript
export function hasWritePermission(
  gitHubRepository: GitHubRepository
): boolean {
  return (
    gitHubRepository.permissions === null ||
    gitHubRepository.permissions !== 'read'
  )
}
```
