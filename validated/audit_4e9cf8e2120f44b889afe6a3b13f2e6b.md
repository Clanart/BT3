## Analog Found

### Title
Malicious `.gitmodules` submodule path can make Desktop open/operate on a directory outside the repository - ([File: app/src/lib/git/diff.ts])

### Summary
The Sherlock report is about an unchecked, attacker-controlled numeric input (`_expiration`) that overflows when combined with another value and thereby defeats a security invariant (a fund-lock check) with no bound validation on the untrusted value. The closest analog in GitHub Desktop is a structurally similar class of bug: an attacker-controlled *string* value from a cloned/fetched repository (a submodule path taken from `.gitmodules`/`git submodule status`) is concatenated into a filesystem path with no bounds validation, unlike the equivalent code paths elsewhere in the app that do validate the resulting path stays inside the repository.

### Finding Description
`buildSubmoduleDiff` computes the filesystem location of a submodule directly from data that comes from the repository content itself: [1](#0-0) 

`path` here originates from `file.path`, which is populated by parsing `git status`/`git diff` output for the working tree, itself driven by the tracked `.gitmodules` file and the submodule's registered path — content that is fully attacker-controlled in a cloned or fetched repository (e.g. a malicious PR branch or a hostile clone URL opened via "Open in Desktop"). The resulting `fullPath = Path.join(repository.path, path)` has **no bounds check** that the result stays inside `repository.path`.

This directly contrasts with other places in the same codebase that handle conceptually identical untrusted path data and explicitly guard against exactly this kind of escape:
- `resolveWithin`, used to validate that a joined path does not escape a root directory (including symlink-based escapes): [2](#0-1) 
- The deep-link `filepath` handler explicitly checks `isAbsolute` and calls `resolveWithin` before doing anything with the resolved path: [3](#0-2) 
- The Copilot conflict-context reader explicitly documents this exact guard ("Guard against path traversal and symlink escapes") and applies `resolveWithin`: [4](#0-3) 

`buildSubmoduleDiff` has no equivalent guard. The computed `fullPath` is then surfaced in the UI as the target of the "Open this submodule on GitHub Desktop" action: [5](#0-4) 

If a `.gitmodules` entry (or the internal `.git/modules/<name>` git configuration `worktree` value, similarly untrusted content shipped with a cloned repo — see the fixture examples showing this literal field) points outside the checkout root via `../../` segments, the value threading through to `fullPath` can resolve outside the repository working directory. [6](#0-5) 

### Impact Explanation
If the downstream handler for `onOpenSubmodule` (not confirmed in this investigation due to tool budget — I was unable to trace it fully into `app.tsx`/`Dispatcher`) treats `fullPath` as a location to add/open as a Desktop repository or to reveal in the file browser, this constitutes exactly the "attacker controls a cloned/fetched repository ... result is ... file read/write outside the repo" category called out as valid impact. Opening or "adding" an attacker-chosen directory as a repository in Desktop could expose sensitive local directories to subsequent Desktop git operations (status, fetch, commit) executed against that directory, and if that directory itself contains a malicious `.git/hooks` tree, subsequent git invocations by Desktop could result in code execution outside the sandbox of the original cloned repo.

### Likelihood Explanation
Exploitability requires only that a victim clone or check out a hostile repository/PR and then view a submodule diff and click "Open this submodule on GitHub Desktop" — a normal, expected user action, not a special/unnatural step, matching the "attacker controls a cloned/fetched repository" precondition allowed by the task's valid-impact criteria. Unlike the sibling code paths (`resolveWithin` guarded), this particular sink has no defense-in-depth, making the bug class plausible; however, I could not confirm end-to-end (i.e., whether the wiring code that consumes `onOpenSubmodule`'s `fullPath` re-validates it before use), so severity and exploitability are not fully confirmed from the indexed code available to me.

### Recommendation
Route the submodule path through `resolveWithin(repository.path, path)` (as already done for the analogous deep-link `filepath` and Copilot conflict-context flows) before constructing `fullPath` in `buildSubmoduleDiff`, and reject/skip the diff (or the "Open Repository" action) if the resolved path falls outside the parent repository.

### Proof of Concept
Not independently reproduced. Conceptually:
1. Attacker publishes a repository/branch containing a `.gitmodules` entry (or a `.git/modules/<name>/config` `worktree` value) with a path such as `path = ../../../../../../../home/victim/.ssh`.
2. Victim clones/fetches and checks out that branch in GitHub Desktop, then views the resulting submodule change, which triggers `buildSubmoduleDiff` and computes `fullPath = Path.join(repository.path, '../../../../../../../home/victim/.ssh')`.
3. Victim clicks "Open this submodule on GitHub Desktop," invoking `onOpenSubmodule(this.props.diff.fullPath)` with a path outside the intended repository.

I was unable to verify within the available tool budget what the ultimate consumer of `onOpenSubmodule` does with this value, so this should be treated as a plausible analog requiring confirmation via a live Devin session with full repo access rather than a fully proven exploit chain.

### Citations

**File:** app/src/lib/git/diff.ts (L798-807)
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```

**File:** app/src/ui/diff/submodule-diff.tsx (L188-211)
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
```

**File:** app/test/fixtures/submodule-basic-setup/_git/modules/foo/submodule/config (L1-8)
```text
[core]
	repositoryformatversion = 0
	filemode = true
	bare = false
	logallrefupdates = true
	ignorecase = true
	precomposeunicode = true
	worktree = ../../../../foo/submodule
```
