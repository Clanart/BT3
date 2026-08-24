Based on my research, the strongest analog in this codebase is the same "the guard exists on one function of a family but not on its siblings" pattern that the Solidity report calls out — but here it's a filesystem path-safety guard rather than a `whenNotPaused` modifier.

### Title
Missing clone-path safety guard on `addWorktree()`/`moveWorktree()` allows worktree creation outside the intended directory - (File: `app/src/lib/git/worktree.ts`)

### Summary
`app/src/lib/git/clone.ts` defines `isClonePathSensitive()` and calls it inside `clone()` [1](#0-0)  specifically to stop a crafted URL/name from resolving a destination path into `~/.ssh`, `~/.gnupg`, `~/.config`, `APPDATA`, etc. The sibling filesystem-writing functions in `app/src/lib/git/worktree.ts` — `addWorktree()` and `moveWorktree()` — take a caller-supplied `path` and pass it straight to `git worktree add`/`git worktree move` with no equivalent check at all [2](#0-1) [3](#0-2) .

### Finding Description
The invariant the codebase already established (and tested — see `app/test/unit/clone-path-safety-test.ts` and `app/test/unit/git/clone-test.ts`) is: "no Desktop-driven git filesystem operation should be allowed to target a sensitive system location derived from attacker/remote-influenced strings." `isClonePathSensitive()` is only wired into `clone()`; it is never imported or referenced anywhere in `worktree.ts` [4](#0-3) .

The worktree path is pre-populated in the UI from data that can originate from a remote/GitHub API object: `AddWorktreeDialog`'s `initialWorktreeName` is set from `branch.nameWithoutRemote` for a normal branch checkout, or from `pullRequest.pullRequestNumber`/`pullRequest.head.ref` context for a PR checkout [5](#0-4) . That name flows into `RepositoryPath`, which derives `fullPath`, and is submitted directly to `addWorktree(repository, fullPath, …)` with no sensitivity check [6](#0-5) .

### Impact Explanation
If a remote-tracking branch or pull-request ref name can be crafted to steer the derived worktree path toward a sensitive directory (e.g. by nested path components), `git worktree add`/`git worktree move` would write repository files (including a `.git` file pointing at the main repo's `.git/worktrees/...`) into that location — a file-write-outside-repo primitive matching the same class of bug the Solidity report flags (a protective check present on one operation but silently absent on its siblings).

### Likelihood Explanation
This is lower-confidence than the fixed `clone()` case for two reasons I could not fully rule out given remaining tool budget: (1) I was unable to confirm from the index whether `RepositoryPath`'s name→path join logic or `sanitizedRefName()` independently blocks `..`/absolute-path components before `addWorktree()` is called (I found `sanitizedRefName` is used only for the branch name field, not for the path field) [7](#0-6) ; (2) git's own ref-name validation rejects `..` components in ref names, which limits (but for `/`-hierarchical branch names does not eliminate) how much an attacker-controlled ref can steer the path. The dialog also displays the resolved destination to the user before submission [8](#0-7) , which is a mitigating factor absent from the original `Crate.sol` report analog.

### Recommendation
Apply the same `isClonePathSensitive()`-style check (exported from `clone.ts` or moved to a shared helper) inside `addWorktree()` and `moveWorktree()` in `app/src/lib/git/worktree.ts` before invoking `git`, mirroring the guard already enforced for `clone()`.

### Proof of Concept
Not independently verified end-to-end due to inability to confirm the exact path-join/sanitization behavior in `repository-path.tsx` within the remaining investigation budget — recommend a Devin session to trace `RepositoryPath`'s `onFullPathChanged` path-construction code and attempt constructing a branch/PR ref name that resolves `fullPath` outside the chosen base directory, then confirm `addWorktree()`/`moveWorktree()` execute unguarded.

### Citations

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L68-79)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }
```

**File:** app/src/lib/git/worktree.ts (L120-143)
```typescript
export async function addWorktree(
  repository: Repository,
  path: string,
  options: {
    /** Branch name used with -b (create new branch) */
    readonly createBranch?: string
    /** Commit-ish to check out (branch name, ref, or SHA) */
    readonly commitish?: string
  } = {}
): Promise<void> {
  const args = ['worktree', 'add']

  if (options.createBranch) {
    args.push('-b', options.createBranch)
  }

  args.push(path)

  if (options.commitish) {
    args.push(options.commitish)
  }

  await git(args, repository.path, 'addWorktree')
}
```

**File:** app/src/lib/git/worktree.ts (L159-169)
```typescript
export async function moveWorktree(
  repository: Repository,
  oldPath: string,
  newPath: string
): Promise<void> {
  await git(
    ['worktree', 'move', oldPath, newPath],
    repository.path,
    'moveWorktree'
  )
}
```

**File:** app/src/ui/toolbar/branch-dropdown.tsx (L414-432)
```typescript
  private onCheckoutInNewWorktree = (branch: Branch) => {
    this.props.dispatcher.closeFoldout(FoldoutType.Branch)
    this.props.dispatcher.showPopup({
      type: PopupType.AddWorktree,
      repository: this.props.repository,
      initialBranchName: branch.name,
      initialWorktreeName: `${this.props.repository.name}-${branch.nameWithoutRemote}`,
    })
  }

  private onCheckoutPRInNewWorktree = (pullRequest: PullRequest) => {
    this.props.dispatcher.closeFoldout(FoldoutType.Branch)
    this.props.dispatcher.showPopup({
      type: PopupType.AddWorktree,
      repository: this.props.repository,
      initialBranchName: pullRequest.head.ref,
      initialWorktreeName: `${this.props.repository.name}-${pullRequest.pullRequestNumber}`,
    })
  }
```

**File:** app/src/ui/worktrees/add-worktree-dialog.tsx (L70-76)
```typescript
  private getEffectiveBranchName(): string {
    const { branchName, worktreeName } = this.state
    if (branchName.length > 0) {
      return branchName
    }
    return sanitizedRefName(worktreeName)
  }
```

**File:** app/src/ui/worktrees/add-worktree-dialog.tsx (L78-110)
```typescript
  private onSubmit = async () => {
    const { fullPath } = this.state

    if (fullPath === null) {
      return
    }

    const effectiveBranchName = this.getEffectiveBranchName()

    this.setState({ creating: true })

    const branch = this.props.allBranches.find(
      b => b.name === effectiveBranchName
    )

    try {
      if (branch?.type === BranchType.Remote) {
        // Remote branch: create a new local branch from the remote ref
        await addWorktree(this.props.repository, fullPath, {
          createBranch: branch.nameWithoutRemote,
          commitish: branch.ref,
        })
      } else if (branch) {
        // Existing local branch: check it out in the new worktree
        await addWorktree(this.props.repository, fullPath, {
          commitish: branch.name,
        })
      } else {
        // New branch: create it in the new worktree
        await addWorktree(this.props.repository, fullPath, {
          createBranch: effectiveBranchName,
        })
      }
```

**File:** app/src/ui/worktrees/add-worktree-dialog.tsx (L166-177)
```typescript
  private renderPathMessage() {
    const { fullPath } = this.state
    if (fullPath === null) {
      return null
    }

    return (
      <div id="add-worktree-path-msg">
        Worktree will be created at <Ref>{fullPath}</Ref>.
      </div>
    )
  }
```
