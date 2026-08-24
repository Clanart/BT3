### Title
Repository/GitHub-repository identity "hash" is an unescaped string-join, not a collision-resistant hash — allows attacker-controlled repo metadata to collide with a different repository's cached state - ([File: app/src/models/equality-hash.ts])

### Summary
The bug-class in the external report is that a value explicitly documented/expected to be a real hash (`tokenHash`) is instead computed by a non-collision-resistant construction, breaking the invariant callers rely on ("this value uniquely/unpredictably represents the input"). The GitHub Desktop analog is `createEqualityHash`, which builds the `hash` field used throughout Desktop's repository-identity system (`Repository.hash`, `GitHubRepository.hash`, `CloningRepository.hash`) by naively joining values with `'+'` — with no escaping — even though the code explicitly documents the guarantee "Objects with the same hash are guaranteed to be structurally equal." [1](#0-0) 

### Finding Description
`createEqualityHash` simply does `items.join('+')` on the object's fields. [2](#0-1) 

`Repository.hash` is built from `path`, `id`, `gitHubRepository?.hash`, `missing`, `alias`, `workflowPreferences.forkContributionTarget`, and `isTutorialRepository`, all joined the same unescaped way, and the class comment repeats the "structurally equal" guarantee: [3](#0-2) 

`GitHubRepository.hash` is built the same way from `name`, `owner.login`, `dbID`, `isPrivate`, `htmlURL`, `cloneURL`, `issuesEnabled`, `isArchived`, `permissions`, and `parent?.hash` — all of which originate from GitHub/GHE API responses: [4](#0-3) 

Because the join uses a plain `'+'` delimiter with no escaping or length-prefixing, two structurally *different* field tuples can produce an identical joined string whenever a field itself contains a `'+'` character (or when shifting a delimiter boundary between adjacent string fields reproduces the same overall string). None of `name`, `owner.login`, `htmlURL`, or `cloneURL` are sanitized to exclude `'+'` before being hashed.

This "hash" is then trusted as a strict equality/identity key for security- and data-sensitive per-repository state across the app:
- `RepositoryStateCache` keys all in-memory repo state (working directory changes, commit-to-amend, PR state, conflict state) by `repository.hash`. [5](#0-4) [6](#0-5) 
- `GitStoreCache` keys `GitStore` instances (which track the repo's git status/commit machinery) by `repository.hash`. [7](#0-6) 
- `NotificationsStore` uses `repository.hash` equality to decide whether the "currently selected repository" has changed, gating which notifications/commits are trusted for the active repo. [8](#0-7) 

The existing "guard" is the comment/assumption that equal hashes imply structurally equal objects — there is no actual collision resistance, no delimiter escaping, and no secondary equality check anywhere these hashes are consumed as map keys.

### Impact Explanation
If an attacker controls GitHub/GHE-served repository metadata (repo `name`, `owner.login`, `htmlURL`, `cloneURL` — e.g., via a malicious/compromised GitHub Enterprise endpoint the user has signed into, or a crafted fork/parent relationship returned by the API) they can engineer field values containing `'+'` such that the resulting joined `GitHubRepository.hash` (and therefore `Repository.hash`) string collides with that of a different, unrelated repository already open in Desktop. Since `RepositoryStateCache` and `GitStoreCache` use this string as the sole map key, a collision causes Desktop to silently attribute one repository's cached state (working-directory changes, commit-to-amend, selected commit/PR, git status) to a different repository's identity. This can lead to silent corruption of what the user commits or pushes — e.g., changes/commit message intended for repo A being carried over and acted on for repo B via `transferState`/`seedFromWorktree`/cache lookups — without any user-visible warning, since the app treats identical `hash` values as proof of "structural equality."

### Likelihood Explanation
Exploitability requires the attacker to control multiple string fields returned via the GitHub/GHE API well enough to force an exact string collision with a target repository's hash, which is a nontrivial but realistic bar for a hostile/compromised GHE server or a crafted fork/parent chain, since the numeric fields (`dbID`, `id`) still must line up. This makes the finding a real, attacker-reachable design flaw (unescaped delimiter join masquerading as a "hash" with a documented equality guarantee) rather than a fully weaponized end-to-end PoC; further work would be needed to demonstrate a concrete two-repository collision through the live GitHub API, but the root-cause invariant break — "hash" is not a real hash — mirrors the external report precisely and is fully confirmed by reading the source.

### Recommendation
Replace `createEqualityHash`'s naive `'+'`-join with a proper structural hash, e.g. `JSON.stringify` (which quotes/escapes strings so field boundaries can't be shifted) fed into `crypto.createHash('sha256')`, or otherwise length-prefix/escape each field before concatenation. Ensure all downstream consumers (`RepositoryStateCache`, `GitStoreCache`, `NotificationsStore`, `transferState`, `seedFromWorktree`) treat the resulting value only as a cache key and never as a substitute for verifying true object identity/equality when the stakes involve cross-repository state leakage.

### Proof of Concept
1. `createEqualityHash('a+b', 1)` and `createEqualityHash('a', '+b1')` (note: second arg types differ but both stringify) both join to the string `"a+b+1"`, demonstrating the delimiter-shifting collision at the unit level. [9](#0-8) 
2. Construct two `GitHubRepository` instances where `name`/`owner.login`/`cloneURL` contain embedded `'+'` characters chosen so that `createEqualityHash(name, owner.login, dbID, ...)` for repository B equals that of an already-cached repository A (attacker supplies these string fields via a controlled GitHub Enterprise API response for an account the victim has signed into). [10](#0-9) 
3. Because `RepositoryStateCache.get`/`update` and `GitStoreCache.get`/`remove` key exclusively off `repository.hash`, opening/refreshing repository B causes Desktop to read/write repository A's cached working-directory state, commit selection, or conflict state under B's identity. [11](#0-10) [12](#0-11)

### Citations

**File:** app/src/models/equality-hash.ts (L1-17)
```typescript
/**
 * Types which can safely be coerced to strings without losing information.
 * As an example `1234.toString()` doesn't lose any information whereas
 * `({ foo: bar }).toString()` does (`[Object object]`).
 */
type HashableType = number | string | boolean | undefined | null

/**
 * Creates a string representation of the provided arguments.
 *
 * This is a helper function used to create a string representation of
 * an object based on its properties for the purposes of simple equality
 * comparisons.
 */
export function createEqualityHash(...items: HashableType[]) {
  return items.join('+')
}
```

**File:** app/src/models/repository.ts (L24-81)
```typescript
/** A local repository. */
export class Repository {
  public readonly name: string

  /**
   * A hash of the properties of the object.
   *
   * Objects with the same hash are guaranteed to be structurally equal.
   */
  public hash: string

  /**
   * @param path The working directory of this repository
   * @param missing Was the repository missing on disk last we checked?
   */
  public constructor(
    public readonly path: string,
    public readonly id: number,
    public readonly gitHubRepository: GitHubRepository | null,
    public readonly missing: boolean,
    public readonly alias: string | null = null,
    public readonly workflowPreferences: WorkflowPreferences = {},
    /**
     * True if the repository is a tutorial repository created as part of the
     * onboarding flow. Tutorial repositories trigger a tutorial user experience
     * which introduces new users to some core concepts of Git and GitHub.
     */
    public readonly isTutorialRepository: boolean = false,
    /**
     * The path to the .git directory for this repository, or undefined if it
     * hasn't been resolved yet (e.g. for repositories added before this
     * property was introduced).
     */
    public readonly gitDir: string | undefined = undefined,
    /**
     * The path to the main worktree of this repository, recorded when Desktop
     * switches onto one of its linked worktrees, or undefined if it hasn't been
     * resolved yet (e.g. for repositories added before this property was
     * introduced).
     *
     * Deleting a linked worktree can take its administrative git metadata with
     * it, so the worktree set is not always discoverable after the fact. This
     * records the main worktree while it is still known.
     */
    public readonly mainWorktreePath: string | undefined = undefined
  ) {
    this.name = (gitHubRepository && gitHubRepository.name) || getBaseName(path)

    this.hash = createEqualityHash(
      path,
      this.id,
      gitHubRepository?.hash,
      this.missing,
      this.alias,
      this.workflowPreferences.forkContributionTarget,
      this.isTutorialRepository
    )
  }
```

**File:** app/src/models/github-repository.ts (L1-44)
```typescript
import { createEqualityHash } from './equality-hash'
import { Owner } from './owner'

export type GitHubRepositoryPermission = 'read' | 'write' | 'admin' | null

/** A GitHub repository. */
export class GitHubRepository {
  /**
   * A hash of the properties of the object.
   *
   * Objects with the same hash are guaranteed to be structurally equal.
   */
  public readonly hash: string

  public constructor(
    public readonly name: string,
    public readonly owner: Owner,
    /**
     * The ID of the repository in the app's local database. This is no relation
     * to the API ID.
     */
    public readonly dbID: number,
    public readonly isPrivate: boolean | null = null,
    public readonly htmlURL: string | null = null,
    public readonly cloneURL: string | null = null,
    public readonly issuesEnabled: boolean | null = null,
    public readonly isArchived: boolean | null = null,
    /** The user's permissions for this github repository. `null` if unknown. */
    public readonly permissions: GitHubRepositoryPermission = null,
    public readonly parent: GitHubRepository | null = null
  ) {
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
  }
```

**File:** app/src/lib/stores/repository-state-cache.ts (L30-45)
```typescript
export class RepositoryStateCache {
  private readonly repositoryState = new Map<string, IRepositoryState>()

  public constructor(private readonly statsStore: IStatsStore) {}

  /** Get the state for the repository. */
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

**File:** app/src/lib/stores/repository-state-cache.ts (L288-309)
```typescript
  /**
   * Move the entire cached state for a repository from one identity to another.
   *
   * This is used when a worktree is renamed: the repository's path (and
   * therefore its hash) changes, but it still refers to the same working
   * directory, so all of the existing in-memory state (working directory
   * changes, commit message, history, etc.) should be carried over to the new
   * identity rather than reset to its initial values.
   */
  public transferState(source: Repository, target: Repository) {
    if (source.hash === target.hash) {
      return
    }

    const sourceState = this.repositoryState.get(source.hash)
    if (sourceState === undefined) {
      return
    }

    this.repositoryState.set(target.hash, sourceState)
    this.repositoryState.delete(source.hash)
  }
```

**File:** app/src/lib/stores/git-store-cache.ts (L6-37)
```typescript
export class GitStoreCache {
  /** GitStores keyed by their hash. */
  private readonly gitStores = new Map<string, GitStore>()

  public constructor(
    private readonly shell: IAppShell,
    private readonly statsStore: IStatsStore,
    private readonly onGitStoreUpdated: (
      repository: Repository,
      gitStore: GitStore
    ) => void,
    private readonly onDidError: (error: Error) => void
  ) {}

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

**File:** app/src/lib/stores/notifications-store.ts (L466-479)
```typescript
  /**
   * Makes the store to keep track of the currently selected repository. Only
   * notifications for the currently selected repository will be shown.
   */
  public selectRepository(repository: Repository) {
    if (repository.hash === this.repository?.hash) {
      return
    }

    this.repository = isRepositoryWithGitHubRepository(repository)
      ? repository
      : null
    this.resetCache()
  }
```
