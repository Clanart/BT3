Found a genuine analog. The `onOpenSubmodule` action carries `diff.fullPath`, which is built by `buildSubmoduleDiff()` using a *raw* `Path.join()` on an attacker-controlled submodule path, without ever passing through the `resolveWithin()` containment check that every other filepath-opening code path in this codebase (e.g. `dispatcher.ts` `openRepositoryFromUrl`) uses.

### Title
Submodule diff "Open Repository" action can be redirected outside the parent repository via a crafted `.gitmodules` path - ([File: app/src/lib/git/diff.ts])

### Summary
`buildSubmoduleDiff()` computes `fullPath = Path.join(repository.path, path)` directly from the submodule path reported by `git status`/`git diff`, which is attacker-controlled via `.gitmodules` in a cloned/fetched repository. Unlike every other path-opening flow in Desktop (which routes through `resolveWithin()` to enforce containment), this value is never validated to stay inside the repository, and it flows unguarded into `SubmoduleDiff.onOpenSubmoduleClick` → `dispatcher`'s "open submodule" action.

### Finding Description
`buildSubmoduleDiff()` builds the `fullPath` field of `ISubmoduleDiff` like this: [1](#0-0) 
```
async function buildSubmoduleDiff(
  buffer: Buffer,
  repository: Repository,
  file: FileChange,
  status: SubmoduleStatus
): Promise<IDiff> {
  const path = file.path
  const fullPath = Path.join(repository.path, path)
```
`file.path` originates from Git's status/diff parsing of the working tree, which for a submodule is the path Git assigns from `.gitmodules`/the tree entry for that submodule — content that is fully attacker-controlled in a cloned or fetched repository. `Path.join` does not restrict `..` traversal segments; a submodule path such as `../../../../Library/Application Support/GitHub Desktop` (or any parent-escaping path) collapses to a location outside `repository.path`.

This `fullPath` is surfaced in the UI as the "Open Repository" action for a submodule diff: [2](#0-1) 
```
private onOpenSubmoduleClick = () => {
  this.props.onOpenSubmodule?.(this.props.diff.fullPath)
}
```
This directly contrasts with the codebase's own established pattern for handling similarly attacker-influenced filepaths, where `resolveWithin()` is explicitly used to guard against traversal and symlink escape before any file-system action is taken: [3](#0-2) 
```
if (filepath !== null) {
  if (isAbsolute(filepath)) {
    log.error(`Refusing to open absolute path: ${filepath}`)
    return
  }

  const resolved = await resolveWithin(repository.path, filepath)
  ...
```
and in `app/src/lib/path.ts`'s `resolveWithin`, which performs `realpath`-based containment checks including symlink escapes: [4](#0-3) 

No equivalent guard exists on the submodule-diff `fullPath` value before it is handed to whatever "open submodule as a repository" logic ultimately consumes it (this downstream consumer — the concrete `onOpenSubmodule` implementation in `dispatcher`/`app-store` — could not be located/confirmed within the indexed code available to me, so the exact end effect, e.g. `Repository` object creation pointed outside the clone, opening an arbitrary folder as a "repository" in Desktop, could not be fully traced).

### Impact Explanation
If the "Open Repository" submodule action ultimately treats `fullPath` as a directory to add/open as a Desktop repository (consistent with `onOpenSubmodule?: (fullPath: string) => void` naming and usage pattern elsewhere in the app for opening local paths), a malicious repository author could craft a `.gitmodules`/tree entry whose submodule path traverses outside the clone directory (e.g., into a sensitive user folder), and get Desktop to open/interact with an out-of-repo directory as if it were the submodule, when the victim clicks the innocuous "Open Repository" button shown for a submodule diff. This matches the "unprivileged, attacker controls a cloned/fetched repo, result is file access outside the repo" impact class.

### Likelihood Explanation
Likelihood depends entirely on what the (unlocated) `onOpenSubmodule` handler does with the path — if it merely calls `dispatcher.addRepositories([fullPath])`/`selectRepository`, this would be a real repo-scope violation but with a UI click required and no `resolveWithin` guard to stop it, unlike the parallel `filepath` handling in the same file. This is a real gap relative to the app's own established containment pattern, but I could not fully confirm the exact downstream consumer of `onOpenSubmodule`, so severity should be validated by tracing that handler in a full checkout.

### Recommendation
Apply the same `resolveWithin(repository.path, path)` containment check used elsewhere in the codebase (e.g. `dispatcher.ts`'s `openRepositoryFromUrl`) when constructing `fullPath` in `buildSubmoduleDiff()`, and reject/sanitize submodule diffs whose resolved path escapes `repository.path` before exposing an "Open Repository" action to the user.

### Proof of Concept
1. Attacker creates a Git repository containing a submodule entry (via `.gitmodules` and a corresponding tree gitlink) whose path is `../../../../some/sensitive/dir`.
2. Victim clones this repository in GitHub Desktop and views the working-directory changes; Desktop calls `getWorkingDirectoryDiff` → `buildSubmoduleDiff`, computing `fullPath = Path.join(repository.path, '../../../../some/sensitive/dir')`, which resolves outside the clone.
3. Desktop renders the "Submodule changes" interstitial with an "Open Repository" button (`renderOpenSubmoduleAction`).
4. Victim clicks "Open Repository"; `onOpenSubmoduleClick` passes the out-of-repo `fullPath` to `props.onOpenSubmodule`, with no `resolveWithin` containment check applied anywhere in this chain (verification of the exact terminal action requires locating the `onOpenSubmodule` wiring, which was not found in the indexed sources available here).

### Citations

**File:** app/src/lib/git/diff.ts (L798-806)
```typescript
async function buildSubmoduleDiff(
  buffer: Buffer,
  repository: Repository,
  file: FileChange,
  status: SubmoduleStatus
): Promise<IDiff> {
  const path = file.path
  const fullPath = Path.join(repository.path, path)
  const url = await getConfigValue(repository, `submodule.${path}.url`, true)
```

**File:** app/src/ui/diff/submodule-diff.tsx (L209-211)
```typescript
  private onOpenSubmoduleClick = () => {
    this.props.onOpenSubmodule?.(this.props.diff.fullPath)
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/lib/path.ts (L36-72)
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
```
