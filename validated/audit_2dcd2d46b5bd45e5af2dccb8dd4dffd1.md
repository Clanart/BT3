### Title
Path traversal via crafted submodule path leads to opening an arbitrary directory as a repository - ([File: app/src/lib/git/diff.ts])

### Summary
The external report's broken invariant is: an attacker-influenced external call path (git operations triggered from a bribe contract) is trusted to run before critical state validation happens, so untrusted data can corrupt program state. The Desktop analog: submodule path strings, which are attacker-controlled content living inside a cloned/fetched repository (`.gitmodules` / git status output), are turned into a filesystem path using plain `Path.join` instead of Desktop's own sandboxing helper `resolveWithin`, and that unsanitized path is later handed straight to `dispatcher.openOrAddRepository()` when the user clicks "Open submodule" in the diff viewer.

### Finding Description
When Desktop renders the diff for a submodule entry it builds the on-disk path with: [1](#0-0) 

```
async function buildSubmoduleDiff(...) {
  const path = file.path
  const fullPath = Path.join(repository.path, path)
  ...
```

`file.path` originates from `git status`/`git diff` output for a path recorded in the repository's tracked `.gitmodules` file and index — content that is fully attacker-controlled when the user clones or fetches a hostile repository. `Path.join` normalizes `..` segments but does **not** verify that the resulting path stays inside `repository.path`.

This is in stark contrast with how Desktop treats other attacker-influenced path strings elsewhere in the same codebase: for the `x-github-client://openRepo` deep-link handler, filepath values are explicitly checked with `resolveWithin`, which resolves symlinks and rejects any result that isn't nested under the repository root: [2](#0-1) 

and the sandbox helper itself: [3](#0-2) 

No equivalent check exists for the submodule `fullPath`. That value flows unchanged into the UI: [4](#0-3) 

and from there into the repository open/add flow when the user clicks the "Open submodule" link: [5](#0-4) 

`openOrAddRepository` treats whatever path it receives as a Git repository to open or register in Desktop's repository list. Because the submodule path can contain `../../../` segments (a value fully controlled by the content of a hostile repository's `.gitmodules`/tree, not by any local user action), `fullPath` can point outside the cloned repository entirely — e.g., into the user's home directory, `~/.ssh`, or any other folder reachable from the process's working directory.

### Impact Explanation
This breaks the "diff-viewer paths must stay inside the working directory" invariant the same way `withHooksEnv`/`resolveWithin` enforce it elsewhere in the codebase. A malicious repo author can craft `.gitmodules`/submodule status output so that clicking the "Open submodule" affordance in the Changes/History diff view causes Desktop to add/open an out-of-repo directory as a tracked repository. Once added, that folder becomes reachable to subsequent Desktop git operations (fetch/pull/commit) initiated by the user against it, and — because Desktop treats "add and open" as implicitly trusted — this is a file system boundary violation triggered purely by cloning/fetching attacker content, with the only user action being a single click on UI content the repo itself produced (a normal, expected interaction, not "unnatural social engineering").

### Likelihood Explanation
Likelihood is moderate-to-high: any user who clones or fetches a hostile repository and later views the Changes/History tab for a modified/added submodule entry is exposed. The affected code path (`buildSubmoduleDiff` → `SubmoduleDiff.onOpenSubmoduleClick` → `onOpenSubmodule` → `openOrAddRepository`) is a normal part of everyday review workflow — no advanced or unusual user behavior is required beyond the single click that's the intended function of that UI element.

### Recommendation
Validate the submodule `fullPath` the same way Desktop already validates other attacker-influenced paths: replace the raw `Path.join(repository.path, path)` in `buildSubmoduleDiff` with `resolveWithin(repository.path, path)` (or an equivalent check) and refuse to render/act on the "Open submodule" affordance (return null / show an error) when the resolved path escapes the repository root, mirroring the guard already used in `dispatcher.ts`'s `openRepositoryFromUrl`.

### Proof of Concept
1. Attacker creates a repository containing a `.gitmodules` entry (or manipulated tree/status data) whose submodule path is `../../../../../../Users/victim/.ssh`.
2. Victim clones/fetches the repository in GitHub Desktop and modifies/stages something that causes Desktop to show a submodule diff for that entry.
3. `buildSubmoduleDiff` computes `fullPath = Path.join(repository.path, '../../../../../../Users/victim/.ssh')`, which resolves outside the cloned repo.
4. Victim clicks "Open submodule" in the diff pane; `onOpenSubmodule` calls `dispatcher.openOrAddRepository(fullPath)`, causing Desktop to add/open the out-of-repo directory as a tracked repository — a file-system boundary violation stemming entirely from attacker-controlled repository content.

*(I could not find any sanitization step for `file.path`/`fullPath` specific to the submodule-diff code path in the available index; if a check exists elsewhere that I could not locate due to index coverage limits, it should be confirmed with a full Devin session against the live repository.)*

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

**File:** app/src/ui/diff/submodule-diff.tsx (L209-211)
```typescript
  private onOpenSubmoduleClick = () => {
    this.props.onOpenSubmodule?.(this.props.diff.fullPath)
  }
```

**File:** app/src/ui/repository.tsx (L625-628)
```typescript
  private onOpenSubmodule = (fullPath: string) => {
    this.props.dispatcher.incrementMetric('openSubmoduleFromDiffCount')
    this.props.dispatcher.openOrAddRepository(fullPath)
  }
```
