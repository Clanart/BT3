Found it. The `RepositoryPath` component (used by `AddWorktreeDialog`) explicitly does **not** sanitize `/` or `\` in the name field, by design, so that users can type `OrgA\RepoB`-style names — and this same component is fed attacker-controlled data through the "Checkout in New Worktree" flow for pull requests.

### Title
Path Traversal via Attacker-Controlled PR Branch Name in "Checkout PR in New Worktree" - (File: `app/src/ui/lib/repository-path.tsx`)

### Summary
`BranchDropdown.onCheckoutPRInNewWorktree` builds the initial worktree directory name directly from `pullRequest.head.ref`, which is the fork branch name supplied by the pull request author — a value fully controlled by any unprivileged GitHub user who opens a PR against the victim's repository. That name is passed as `initialWorktreeName`/`initialName` into `RepositoryPath`, whose `safeDirectoryName()` helper intentionally leaves `/` and `\` untouched "so users can type `OrgA\RepoB`", and does not strip `..` segments at all. [1](#0-0) [2](#0-1) 

### Finding Description
- `onCheckoutPRInNewWorktree` sets `initialWorktreeName: pullRequest.head.ref` without any sanitization of path-traversal sequences: [1](#0-0) 
- `RepositoryPath` seeds its `name` state directly from `props.initialName` (`initialWorktreeName` above), and computes the final path via `Path.join(path, safeDirectoryName(name))`: [3](#0-2) 
- `safeDirectoryName` only strips Windows-illegal characters (`<>:"|?*`) and trailing whitespace on Windows; it explicitly does not touch `/` or `\`, and never rejects `..` components — unlike the hardened `sanitizeCloneName()` used for the clone-URL flow (which splits on `/\:` and rejects `..`/`.`/empty components): [2](#0-1) [4](#0-3) 
- Because a git ref/branch name is permitted to contain `/` (e.g., `feature/x`) but Git also permits crafted refs containing literal `..` components in some contexts, and because Desktop performs no rejection of `..` in this name field at all, a fork branch named such that `head.ref` resolves (after `Path.join`) to a path outside the intended parent directory can influence the resulting worktree destination. The `AddWorktree` dialog then calls `addWorktree(repository, fullPath, …)`, which passes `fullPath` unchecked straight to `git worktree add <path>`: [5](#0-4) [6](#0-5) 
- Note the codebase already contains a purpose-built guard, `resolveWithin`/`resolveWithinPosix`, that verifies a resolved path stays under a root (used elsewhere for defense-in-depth against traversal and symlink escapes): [7](#0-6)  — but neither `RepositoryPath.getFullPath()` nor `AddWorktreeDialog.onSubmit` apply it before invoking `addWorktree`.

### Impact Explanation
If exploitable, this would let a pull-request author (fully unprivileged relative to the victim maintainer's machine) influence where `git worktree add` materializes a new working directory on the victim's filesystem when the victim uses "Checkout PR in New Worktree", potentially writing a worktree (and thus attacker-controlled file contents from their fork branch) to an unintended location outside the chosen parent directory. This matches the "attacker controls a fetched/PR object → corrupts a path/identifier used for filesystem operations" primitive analogous to the referenced report's untrusted-input-as-identifier issue.

### Likelihood Explanation
Moderate-to-low confidence: this requires that git refs can actually contain a component that, when concatenated via `Path.join(path, name)`, escapes `path` (git's own ref-name rules forbid literal `..` path components in ref names via `check_refname_format`, which would block the most direct payload). I was not able to fully verify, without running code, whether any of git's still-permitted syntactic tricks (e.g., an untypical hostname/ref combination, or relying on the fact that `initialWorktreeName` also concatenates `repository.name` and `pullRequest.pullRequestNumber` around the ref, at `onCheckoutInNewWorktree`/`onCheckoutPRInNewWorktree`) can defeat both git's ref validation and still produce a traversal-capable string once passed through `safeDirectoryName`. This uncertainty means the exploit path is plausible but not confirmed as concretely working end-to-end from the local code alone.

### Recommendation
- Apply the same hardening used for clone paths (`sanitizeCloneName`) to `RepositoryPath`'s name field, or explicitly reject `..`/empty components in `safeDirectoryName`.
- Before calling `addWorktree`, validate the resolved `fullPath` with the existing `resolveWithin`/`resolveWithinPosix` helper against the chosen base directory, mirroring the backstop already added in `app/src/lib/git/clone.ts` (`isClonePathSensitive`).

### Proof of Concept
Not fully constructible from local code alone — the concrete failure requires confirming that a valid (git-accepted) `pullRequest.head.ref` string can, after being passed through `safeDirectoryName` and `Path.join`, resolve outside the intended parent directory. This could not be verified without executing/testing git's ref-name validation rules, which are outside the indexed code available to me.

Given the incomplete verification of an actual escaping payload, I want to flag this explicitly rather than overstate certainty: the sanitization gap in `safeDirectoryName` (no `..`/traversal handling, unlike `sanitizeCloneName`) is real and demonstrable in isolation, but a full working exploit chain through the PR-worktree flow is not confirmed here.

### Citations

**File:** app/src/ui/toolbar/branch-dropdown.tsx (L424-432)
```typescript
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

**File:** app/src/ui/lib/repository-path.tsx (L11-25)
```typescript
// We use this instead of sanitizedRepositoryName because it deals with
// valid repository names on GitHub.com but here we only care about whether
// we'll be able to create a directory with the given name. If a user
// creates a repository with a name that GitHub.com doesn't like here it'll
// get sanitized in the Publish dialog later on.
//
// Note that we don't sanitize `\` or `/` here since we use `Path.join` to
// create the full path and that will handle those characters appropriately
// letting users type something like OrgA\RepoB and have the new repo be
// created in the OrgA folder.
//
// macOS and Linux are way more allowing so there's no need to sanitize
const safeDirectoryName = (name: string) => {
  return __WIN32__ ? name.replace(/[<>:"|?*]/g, '-').replace(/\s+$/, '') : name
}
```

**File:** app/src/ui/lib/repository-path.tsx (L93-129)
```typescript
  public constructor(props: IRepositoryPathProps) {
    super(props)
    this.state = {
      name: props.initialName ?? '',
      path: props.initialPath ?? null,
    }
  }

  public async componentDidMount() {
    if (this.state.path === null) {
      const path = await getDefaultDir()
      this.setState({ path }, () => this.notifyAll())
    } else {
      this.notifyAll()
    }
  }

  /**
   * Emit the current name, path, and full path to the parent. Called
   * once on mount (after default path loading if needed).
   */
  private notifyAll() {
    const { name, path } = this.state
    this.props.onNameChanged?.(name)
    if (path !== null) {
      this.props.onPathChanged?.(path)
    }
    this.emitFullPath()
  }

  private getFullPath(): string | null {
    const { name, path } = this.state
    if (path === null || path.length === 0 || name.trim().length === 0) {
      return null
    }
    return Path.join(path, safeDirectoryName(name))
  }
```

**File:** app/src/lib/remote-parsing.ts (L88-116)
```typescript
export function sanitizeCloneName(name: string): string | null {
  const components = name.split(/[/\\:]/)

  let lastComponent = ''
  for (let i = components.length - 1; i >= 0; i--) {
    if (components[i].length > 0) {
      lastComponent = components[i]
      break
    }
  }

  if (lastComponent.length === 0) {
    return null
  }

  if (lastComponent.endsWith('.git')) {
    lastComponent = lastComponent.slice(0, -4)
  }

  if (
    lastComponent === '..' ||
    lastComponent === '.' ||
    lastComponent.length === 0
  ) {
    return null
  }

  return lastComponent
}
```

**File:** app/src/ui/worktrees/add-worktree-dialog.tsx (L93-110)
```typescript
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

**File:** app/src/lib/path.ts (L36-100)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}

/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
 *
 * This method will resolve paths using the current platform path
 * structure.
 *
 * @param rootPath     The path to the root path. The resolved path
 *                     is guaranteed to reside at, or underneath this
 *                     path.
 * @param pathSegments One or more paths to join with the root path
 */
export function resolveWithin(
  rootPath: string,
  ...pathSegments: string[]
): Promise<string | null> {
  return _resolveWithin(rootPath, pathSegments)
}
```
