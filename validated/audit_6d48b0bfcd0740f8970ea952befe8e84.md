This confirms the analog. Let me finalize with the exact `removeRepository` code path.### Title
Trusting an "unsafe" repository directory permanently whitelists it in global Git config with no revoke path - (File: `app/src/lib/git/config.ts`)

### Summary
GitHub Desktop's mitigation for Git's `safe.directory` protection lets a user grant trust to a repository path that is "owned by another user" (i.e. potentially attacker-controlled, e.g. a cloned/fetched repo, a mounted share, or a directory reached via a "Clone" deep link). The trust grant is implemented as a one-way, permanent addition to the user's **global** `~/.gitconfig` `safe.directory` list via `addSafeDirectory` [1](#0-0) , invoked from `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory` [2](#0-1) [3](#0-2) . There is no corresponding `removeSafeDirectory`/revoke function anywhere in the codebase, and removing the repository from Desktop (`removeRepository`) does not touch this global git config entry.

### Finding Description
Git's own `safe.directory` mechanism exists specifically to stop Git from running (and thus executing any repo-controlled hooks/config) in a directory owned by a different user — a scenario that commonly arises from a shared drive, a container mount, or a maliciously staged clone target. GitHub Desktop surfaces this as an "unsafe repository" warning and offers a one-click "Trust Repository" action that calls `addSafeDirectory(path)`:

```ts
export async function addSafeDirectory(path: string) {
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }
  await addGlobalConfigValueIfMissing('safe.directory', path)
}
``` [4](#0-3) 

This appends the path to the **global**, user-wide `~/.gitconfig`, not anything scoped to the Desktop application's own state store. The grant is irreversible from within the app: a codebase-wide search shows `addSafeDirectory`/`safe.directory` referenced only in `config.ts`, the two "trust directory" UI call sites, and tests — there is no `removeSafeDirectory`, no `--unset` call, and no UI affordance to untrust a path [2](#0-1) [3](#0-2) .

Critically, removing the repository from Desktop's own list (`_removeRepository` / `RepositoriesStore`) only deletes Desktop's local IndexedDB record of the repository [5](#0-4) ; it never calls into `config.ts` to strip the corresponding entry from `safe.directory`. So once a user (often instinctively, just to get past the warning and open a cloned repo) clicks "Trust Repository", that exact path stays whitelisted in the global gitconfig forever, for every future git invocation on that machine — inside or outside Desktop — regardless of whether the repository is later deleted, relocated, or removed from Desktop.

This mirrors the audited bug class exactly: a one-way "grant" primitive (`allowedBurningAddresses[addr] = true`) with no matching "revoke" primitive, allowing a mistaken/attacker-induced grant to persist indefinitely and be exploited later.

### Impact Explanation
An attacker who controls a cloned/fetched repository or a directory reached through a deep link only needs the user to accept the "Trust Repository" prompt once (a very low bar, since the prompt has to be dismissed to open the repo at all, and its wording emphasizes "if you trust the owner" without clarifying the grant is permanent and global). After that:
- The path is permanently exempted from Git's ownership-mismatch safety check, in the user's real `~/.gitconfig`, not just inside Desktop's sandbox.
- If that same path is later repopulated by an attacker (e.g. reusing a temp/shared directory, a removable drive, or a path an attacker can predict/reclaim), any Git operation — from Desktop or from the command line — will silently trust it again, re-enabling any repo-config-driven code execution vector (e.g. malicious `core.fsmonitor`, hooks, or `include.path` directives) that the `safe.directory` check was designed to block.
- Because the exemption is global and outlives the Desktop repository record, users have no visibility into which paths are still trusted, and no in-app way to audit or clean the list.

### Likelihood Explanation
Any workflow that lands a user on the "unsafe repository" screen (cloning into a pre-existing directory owned by another user, opening a repo on a shared/mounted volume, or following a deep link that points Desktop at such a path) naturally funnels the user toward clicking "Trust Repository," since that is the only way to proceed besides "Remove." Given this is the expected/blessed path through the UI (not a bypass), and the permanence is not surfaced to the user, likelihood of accumulating stale global trust entries is high in any environment with shared/temporary directories (CI checkouts, shared build machines, USB drives, container bind-mounts).

### Recommendation
Add a revoke path symmetric to the grant:
- Implement `removeSafeDirectory(path)` in `app/src/lib/git/config.ts` that runs `git config --global --unset-all safe.directory <path>`.
- Call it from `RepositoriesStore`/`_removeRepository` when a repository backed by a previously-trusted unsafe path is removed from Desktop, and/or expose an explicit "Untrust this directory" action in Preferences so users can audit and revoke prior grants.
- Consider scoping trust decisions to Desktop's own persisted state (with a re-check against the live gitconfig) rather than relying solely on Git's global, ambient `safe.directory` list, so Desktop can enforce revocation even if the git config entry lingers.

### Proof of Concept
1. Clone or point Desktop at a directory owned by a different user (or simulate via a container/shared mount) so `getRepositoryType` returns `kind: 'unsafe'`.
2. In `MissingRepository` or `AddExistingRepository`, click "Trust Repository" → `onTrustDirectory` → `addSafeDirectory(unsafePath)` runs, adding the path to `~/.gitconfig`'s `safe.directory` [3](#0-2) [4](#0-3) .
3. Remove the repository from Desktop via `dispatcher.removeRepository` [6](#0-5) .
4. Inspect `~/.gitconfig`: the `safe.directory=<path>` entry is still present.
5. Have an attacker (or the same shared environment) repopulate that exact path with a new, malicious repository. Any subsequent `git` command against that path — from Desktop or the shell — will treat it as trusted and skip the ownership-mismatch safety check, with no re-prompt and no record in Desktop of the outstanding grant.

### Citations

**File:** app/src/lib/git/config.ts (L176-189)
```typescript
/**
 * Adds a path to the `safe.directories` configuration variable if it's not
 * already present. Adding a path to `safe.directory` will cause Git to ignore
 * if the path is owner by a different user than the current.
 */
export async function addSafeDirectory(path: string) {
  // UNC-paths on Windows need to be prefixed with `%(prefix)/`, see
  // https://github.com/git-for-windows/git/commit/e394a16023cbb62784e380f70ad8a833fb960d68
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }

  await addGlobalConfigValueIfMissing('safe.directory', path)
}
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L69-77)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingRepository: true })
    const { repositoryUnsafePath, path } = this.state
    if (repositoryUnsafePath) {
      await addSafeDirectory(repositoryUnsafePath)
    }
    await this.validatePath(path)
    this.setState({ isTrustingRepository: false })
  }
```

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```

**File:** app/src/ui/missing-repository.tsx (L161-163)
```typescript
  private remove = () => {
    this.props.dispatcher.removeRepository(this.props.repository, false)
  }
```

**File:** app/src/lib/stores/repositories-store.ts (L36-59)
```typescript
/** The store for local repositories. */
export class RepositoriesStore extends TypedBaseStore<
  ReadonlyArray<Repository>
> {
  // Key-repo ID, Value-date
  private lastStashCheckCache = new Map<number, number>()

  /**
   * Key is the GitHubRepository id, value is the protected branch count reported
   * by the GitHub API.
   */
  private branchProtectionSettingsFoundCache = new Map<number, boolean>()

  /**
   * Key is the lookup by the GitHubRepository id and branch name, value is the
   * flag whether this branch is considered protected by the GitHub API
   */
  private protectionEnabledForBranchCache = new Map<string, boolean>()

  private emitQueued = false

  public constructor(private readonly db: RepositoriesDatabase) {
    super()
  }
```
