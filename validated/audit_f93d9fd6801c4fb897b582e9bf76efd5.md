Based on my investigation, this is a strong analog to trace through: `buildSubmoduleDiff` in `app/src/lib/git/diff.ts` constructs a filesystem path from an attacker-controlled submodule path without the traversal/symlink guard (`resolveWithin`) that other similarly-sensitive code paths in the same codebase use.

### Title
Path traversal via unsanitized submodule path in `buildSubmoduleDiff` leads to `shell.showItemInFolder` opening arbitrary directory outside repo - ([File: app/src/lib/git/diff.ts])

### Summary
`buildSubmoduleDiff` derives the on-disk path to a submodule directly from the file path reported by `git status`/`git diff`, via `Path.join(repository.path, path)`, and stores it as `ISubmoduleDiff.fullPath` [1](#0-0) . Unlike other code that resolves untrusted repository-relative paths (e.g. `buildConflictContext`, which explicitly calls `resolveWithin` to guard "against path traversal and symlink escapes"), this path is never validated to stay inside `repository.path` [2](#0-1) .

### Finding Description
`file.path` for a submodule entry comes from parsing raw `git status --porcelain=2` output in `app/src/lib/status-parser.ts`, where the path is extracted via regex/substring with no rejection of `..` segments [3](#0-2) . That value flows unchanged into `WorkingDirectoryFileChange`/`FileChange.path`, and then into `buildSubmoduleDiff`, which does `const fullPath = Path.join(repository.path, path)` and returns it as `diff.fullPath` [4](#0-3) . This value is surfaced to the UI in `SubmoduleDiff`, where clicking "Open Repository" invokes `onOpenSubmodule(this.props.diff.fullPath)` [5](#0-4) .

This mirrors the broken-invariant pattern in the report: an internal assumption ("this path is always a submodule path relative to and contained within the repository") is trusted without verification, and the attacker (via a crafted `.gitmodules`/tree entry in a cloned/fetched repository) controls the value that violates it.

### Impact Explanation
If a malicious repository can cause `file.path` for a submodule entry to contain traversal sequences (or resolve, via nested submodule directory names, outside `repository.path`), `Path.join` will happily produce a path outside the repository root, since `Path.join` normalizes `..` without any bounds check — this is exactly the class of bug `resolveWithin` was introduced elsewhere in the codebase to prevent. Depending on how `onOpenSubmodule` is wired in the app store/dispatcher, this could result in opening/exposing an arbitrary directory on the user's filesystem (`shell.showItemInFolder`-style disclosure) or, if the resulting path is later used to run git operations "in" that directory, potentially broader consequences.

### Likelihood Explanation
Likelihood is currently **unverified/low-to-moderate** and should be treated as **uncertain** rather than confirmed: I was not able to fully verify within this session (a) whether git itself rejects tree/status entries with `..` path components before they ever reach Desktop's parser (which would neutralize the primitive at the git layer), and (b) the exact implementation of `onOpenSubmodule` in `app-store.ts`/`dispatcher.ts` to know whether it performs its own containment check before acting on `fullPath`. Both of those would need to be confirmed by a background agent with deeper access, since the local index only returned partial matches for `fullPath` usage in `app-store.ts` and `dispatcher.ts` (6 and 5 matches respectively) that I could not fully read in this session.

### Recommendation
Route `buildSubmoduleDiff`'s path construction through the same `resolveWithin(repository.path, path)` guard used in `app/src/lib/copilot-conflict-context.ts` [2](#0-1)  and `app/src/lib/path.ts`, and treat a `null` result (path escapes the repo) as an unrenderable/unopenable submodule diff rather than passing the raw joined path to the UI action handler.

### Proof of Concept
Conceptual (not fully verified against real git behavior in this session):
1. Attacker crafts a repository with a submodule entry whose registered path, as reported by `git status --porcelain=2`, contains `../` sequences or otherwise resolves outside the repository working directory when joined naively.
2. Victim clones/fetches this repository and views the Changes list; Desktop calls `getStatus` → `buildStatusMap` → eventually `buildSubmoduleDiff`, producing `diff.fullPath = Path.join(repository.path, path)` outside `repository.path`.
3. Victim clicks "Open Repository" in the submodule diff view, triggering `onOpenSubmodule(fullPath)`, potentially opening/exposing a directory outside the intended repository.

This PoC requires confirmation of step 1 (whether git/dugite can actually surface such a path through `git status`) and step 3's exact downstream handler — both flagged above as unresolved in this investigation.

### Citations

**File:** app/src/lib/git/diff.ts (L798-841)
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

**File:** app/src/lib/status-parser.ts (L172-182)
```typescript
function parseUntrackedEntry(field: string): IStatusEntry {
  const path = field.substring(2)
  return {
    kind: 'entry',
    // NOTE: We return ?? instead of ? here to play nice with mapStatus,
    // might want to consider changing this (and mapStatus) in the future.
    statusCode: '??',
    submoduleStatusCode: '????',
    path,
  }
}
```

**File:** app/src/ui/diff/submodule-diff.tsx (L209-211)
```typescript
  private onOpenSubmoduleClick = () => {
    this.props.onOpenSubmodule?.(this.props.diff.fullPath)
  }
```
