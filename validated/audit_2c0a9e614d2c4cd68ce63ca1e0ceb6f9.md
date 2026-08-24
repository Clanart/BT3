### Title
Untrusted submodule path from a malicious repo can point outside the repository, allowing the "Open Submodule" action to trick users into adding an attacker-chosen local directory as a trusted Desktop repository - (File: `app/src/lib/git/diff.ts`)

### Summary
The Sherlock report's broken invariant is: a "trusted" operation (`claimAndExitFor`) accepts an attacker/caller-supplied destination (`to`) without constraining it to the legitimate target (`account`), letting a privileged actor redirect funds anywhere. The same shape of bug exists in Desktop's submodule-diff code path: the "full path" used to drive the "Open this submodule" trusted local-filesystem action is derived directly from the submodule path string reported by Git for a cloned/fetched repository, and is joined onto the repository root with a plain `Path.join` — with none of the containment checks (`resolveWithin`, `isAbsolute` rejection) that Desktop already applies to the structurally identical "open file from URL" flow.

### Finding Description
When Desktop renders a diff for a submodule entry, `buildSubmoduleDiff()` computes the path that will later be used to open/add that submodule as a repository: [1](#0-0) 

```
const path = file.path
const fullPath = Path.join(repository.path, path)
```

`file.path` originates from Git's own reporting of the submodule's path (from the index/`.gitmodules`/status output of a repository the user cloned or fetched) — i.e., content fully controlled by whoever authored the remote repository. That value is placed into `ISubmoduleDiff.fullPath` [2](#0-1)  and surfaced to the UI's "Open this submodule on GitHub Desktop" button: [3](#0-2) 

Clicking it calls `onOpenSubmodule(fullPath)`, which flows to `Dispatcher.openOrAddRepository(fullPath)` unmodified: [4](#0-3) [5](#0-4) 

Nowhere in this chain is `fullPath` validated to still be *inside* `repository.path`. Contrast this with the nearly identical "open a file relative to a repository" flow triggered from a `x-github-client://` deep link, where Desktop explicitly rejects absolute paths and calls `resolveWithin(repository.path, filepath)` before ever touching the filesystem: [6](#0-5) 

and the equivalent `resolveWithin` helper that is used elsewhere in the codebase specifically to stop `..`/symlink traversal out of a repository root: [7](#0-6) 

`buildSubmoduleDiff` skips this guard entirely, so a crafted submodule path (e.g. containing `..` segments, or a path component that is a symlink pointing outside the repo — the exact case `resolveWithin`'s own test suite is built to defend against) can cause `fullPath` to resolve outside the cloned repository.

### Impact Explanation
This matches the report's bug class: a function meant to act on a repository-scoped resource is fed an attacker-controlled "destination" and used to drive a trust-relevant action without confirming it stays within the expected boundary. Here the consequence is that a malicious repository author can steer the "Open Submodule" button toward an arbitrary local path on the victim's machine, invoking the "Add Repository" flow (`PopupType.AddRepository` → `_addRepositories`) against that path. While `_addRepositories`/`getRepositoryType` will refuse anything that isn't itself a Git working directory, this still lets an attacker probe for and coerce the victim into onboarding an unintended local directory as a Desktop-managed repository via a single click inside a diff view the victim already trusts, which is inconsistent with the containment guarantee Desktop enforces in the sibling "open filepath from URL" code path.

### Likelihood Explanation
Requires the victim to open a malicious/compromised repository (or fork/branch containing a manipulated submodule entry) in Desktop, view its Changes/History for the submodule row, and click the "Open Repository" suggested action — a plausible, low-friction interaction for a feature specifically presented to users when submodules change. No local access, credentials, or elevated privileges are needed; the only trust boundary crossed is "clone/inspect an attacker-controlled repository," which is squarely in-scope per the task's valid-impact criteria.

### Recommendation
In `buildSubmoduleDiff()` (`app/src/lib/git/diff.ts`), replace the raw `Path.join(repository.path, path)` with the same `resolveWithin(repository.path, path)` containment check already used for the `open-repository-from-url` filepath handling, and treat a `null` result (path escapes the repo) as "submodule cannot be opened" rather than passing an unvalidated path down into `openOrAddRepository`.

### Proof of Concept
1. Attacker creates a repository whose `.gitmodules`/index submodule entry has a path such that Git reports it (via `git status`) with a value that normalizes outside the repo root when joined with `Path.join` (e.g., a symlinked directory component, mirroring the traversal case already covered by `resolveWithin`'s own unit tests: `app/test/unit/path-test.ts:65-101`).
2. Victim clones/fetches this repository in GitHub Desktop and opens the Changes view showing the submodule diff.
3. `getWorkingDirectoryDiff` → `buildSubmoduleDiff` computes `fullPath = Path.join(repository.path, path)`, which resolves outside `repository.path`.
4. Victim clicks "Open this submodule on GitHub Desktop" (`submodule-diff.tsx:209-211`).
5. `Dispatcher.openOrAddRepository(fullPath)` opens the "Add Repository" dialog pre-populated with the out-of-repo path, with no prior containment check — unlike the equivalent `filepath` handling in `openRepositoryFromUrl`, which explicitly calls `resolveWithin` and rejects absolute/escaping paths.

Note: I could not fully verify, within the available index, the exact low-level parsing routine that produces `file.path` for submodule status entries in `app/src/lib/status-parser.ts` (only match counts were retrievable, not the parsing logic itself), so the precise character set Git allows through into `file.path` before Desktop consumes it is not fully confirmed here. If a Devin session is needed to inspect `app/src/lib/status-parser.ts` in full to confirm exactly which traversal payloads survive Git's own status/porcelain output, that would close the remaining gap.

### Citations

**File:** app/src/lib/git/diff.ts (L798-842)
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

  let oldSHA = null
  let newSHA = null

  if (
    status.commitChanged ||
    file.status.kind === AppFileStatusKind.New ||
    file.status.kind === AppFileStatusKind.Deleted
  ) {
    const diff = buffer.toString('utf-8')
    const lines = diff.split('\n')
    const baseRegex = 'Subproject commit ([^-]+)(-dirty)?$'
    const oldSHARegex = new RegExp('-' + baseRegex)
    const newSHARegex = new RegExp('\\+' + baseRegex)
    const lineMatch = (regex: RegExp) =>
      lines
        .flatMap(line => {
          const match = line.match(regex)
          return match ? match[1] : []
        })
        .at(0) ?? null

    oldSHA = lineMatch(oldSHARegex)
    newSHA = lineMatch(newSHARegex)
  }

  return {
    kind: DiffType.Submodule,
    fullPath,
    path,
    url,
    status,
    oldSHA,
    newSHA,
  }
}
```

**File:** app/src/models/diff/diff-data.ts (L91-111)
```typescript
export interface ISubmoduleDiff {
  readonly kind: DiffType.Submodule

  /** Full path of the submodule */
  readonly fullPath: string

  /** Path of the repository within its container repository */
  readonly path: string

  /** URL of the submodule */
  readonly url: string | null

  /** Status of the submodule */
  readonly status: SubmoduleStatus

  /** Previous SHA of the submodule, or null if it hasn't changed */
  readonly oldSHA: string | null

  /** New SHA of the submodule, or null if it hasn't changed */
  readonly newSHA: string | null
}
```

**File:** app/src/ui/diff/submodule-diff.tsx (L188-212)
```typescript
  private renderOpenSubmoduleAction() {
    // If no url is found for the submodule, it means it can't be opened
    // This happens if the user is looking at an old commit which references
    // a submodule that got later deleted.
    if (this.props.diff.url === null) {
      return null
    }

    return (
      <span>
        <SuggestedAction
          title="Open this submodule on GitHub Desktop"
          description="You can open this submodule on GitHub Desktop as a normal repository to manage and commit any changes in it."
          buttonText={__DARWIN__ ? 'Open Repository' : 'Open repository'}
          type="primary"
          onClick={this.onOpenSubmoduleClick}
        />
      </span>
    )
  }

  private onOpenSubmoduleClick = () => {
    this.props.onOpenSubmodule?.(this.props.diff.fullPath)
  }
}
```

**File:** app/src/ui/repository.tsx (L625-628)
```typescript
  private onOpenSubmodule = (fullPath: string) => {
    this.props.dispatcher.incrementMetric('openSubmoduleFromDiffCount')
    this.props.dispatcher.openOrAddRepository(fullPath)
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1953-1972)
```typescript
    if (repository === null) {
      return
    }

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2235-2250)
```typescript
  public async openOrAddRepository(path: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r => r.path === path)

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.showPopup({
        type: PopupType.AddRepository,
        path,
      })
    })
  }
```

**File:** app/src/lib/path.ts (L36-71)
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
```
