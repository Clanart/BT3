Confirmed: `removeSafeDirectory` does not exist anywhere in the codebase — `addSafeDirectory` is a one-way, permanent grant with no corresponding revocation path.

### Title
Trust granted via "Trust Repository" (`safe.directory`) is never revoked when the repository is removed, letting an attacker who later controls the same filesystem path silently regain code-execution trust - (File: `app/src/lib/git/config.ts`)

### Summary
When Desktop encounters a repository owned by a different user than the current OS user, Git treats it as "unsafe" and refuses to operate on it (to prevent hooks/config in an attacker/other-user-owned directory from executing code as the victim). Desktop lets the user click "Trust Repository", which permanently adds the path to the global `safe.directory` list via `addSafeDirectory` [1](#0-0) . There is no corresponding function to remove a path from `safe.directory` anywhere in the codebase (confirmed by searching for `removeSafeDirectory`), and the "Remove" action on a repository (`missing-repository.tsx`'s `remove` / `dispatcher.removeRepository`) never touches this list [2](#0-1) .

### Finding Description
This mirrors the reported bug class exactly: an entry that grants elevated trust/privilege is added to a persistent set (`Set.index` in the Solidity report; `safe.directory` global git config here), and removal of the "member" (the repository) from Desktop's own repository list never resets/removes the corresponding entry in that trust set. The trust check (`getRepositoryType` calling into Git's own `safe.directory` evaluation, surfaced in Desktop as `type.kind === 'unsafe'`) only looks at whether the path string is present in the config — exactly like `has()` in `AsSequentialSet.sol` only checking `index[o] > 0`. Once the path string is present, it stays "trusted" forever, regardless of whether Desktop still associates that path with the repository the user originally trusted.

Attack scenario: A user clones/adds a repository owned by another OS user (or on a network share) at path `P`, sees the "unsafe" warning, and clicks "Trust Repository" [3](#0-2) , which persists `P` in the global `safe.directory` git config forever. The user later removes that repository from Desktop (`missing-repository.tsx` `remove`) [2](#0-1)  and deletes the folder. If any other actor (a different local account on a shared machine, a container, a restored backup, or a re-cloned/attacker-influenced directory) later places a *different*, attacker-controlled Git repository at the exact same path `P`, Git will silently treat it as safe — no "unsafe repository" prompt will be shown, and Desktop will run Git commands (including anything that triggers hooks, `core.fsmonitor`, `.gitattributes` filters, etc.) against attacker content without any user confirmation, because `getRepositoryType` will simply report `kind: 'regular'` instead of `kind: 'unsafe'`.

### Impact Explanation
The `safe.directory` trust bypass exists specifically to gate execution of repository-controlled Git hooks/config against a directory not owned by the current user. Because trust is never revoked when Desktop stops tracking the repository, the security boundary silently and permanently disappears for that path, defeating the entire purpose of the unsafe-directory warning for any future occupant of that path. This can lead to code execution via Git hooks/filters running unprompted the moment Desktop is pointed at (or auto-detects) a repo at the previously-trusted path.

### Likelihood Explanation
Requires a specific precondition — path reuse after a repository is removed and a different (attacker-influenced) repo occupies the same path later — so it is not attacker-triggerable on demand for an arbitrary victim. It is a realistic scenario on shared machines, in CI/build agents that reuse workspace directories, or in cloud/VM images restored from a snapshot where the safe-directory global config is baked into the user profile. This is a real, unaddressed gap rather than a theoretical one, since there is no removal API at all in the current code.

### Recommendation
Add a `removeSafeDirectory(path)` counterpart to `addSafeDirectory` in `app/src/lib/git/config.ts` (using `git config --global --unset safe.directory <path>` or rewriting the multi-valued list), and invoke it from the repository removal flow (`dispatcher.removeRepository` / `AppStore._removeRepository`) whenever the repository being removed was previously marked as `isTrustedInDesktop`/had gone through the trust flow, mirroring the fix pattern in the original report: resetting the "membership" record at the same time the item is removed from the primary collection.

### Proof of Concept
1. On a shared/multi-user machine, as user A, clone or add a repository at `/shared/repo` owned by user B (or simulate via `chown`), triggering Desktop's "unsafe" warning.
2. Click "Trust Repository" in `AddExistingRepository`/`MissingRepository` — this runs `addSafeDirectory('/shared/repo')` [4](#0-3) , permanently adding the path to `~/.gitconfig`'s `safe.directory`.
3. Remove the repository from Desktop ("Remove" button) — observe that `git config --global --get-all safe.directory` still lists `/shared/repo`.
4. Delete `/shared/repo` and have user B (or an attacker with write access to that path) place a new, malicious Git repository (with a malicious `post-checkout`/`pre-commit` hook or `.gitattributes` filter) at the exact same path.
5. Re-open that path in Desktop as user A: `getRepositoryType` reports `kind: 'regular'` (not `'unsafe'`) because the path is still in `safe.directory`, so no trust prompt appears and Desktop proceeds to run Git operations against the attacker's repository unprompted [1](#0-0) .

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
