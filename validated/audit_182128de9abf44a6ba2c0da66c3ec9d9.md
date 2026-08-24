## Title
Unsafe path join in `getResolutionDiff` bypasses the app's own path-traversal/symlink guard - ([File: app/src/lib/git/diff.ts])

### Summary
The Monad report's bug class is a broken invariant: two code paths are supposed to enforce the same size limit, but one uses the correct config-derived value while the other hardcodes a stale, mismatched constant, so the effective enforcement diverges from what the rest of the system assumes. Desktop's Copilot merge-conflict feature has the same class of divergence, but for a path-safety invariant instead of a size limit: one function resolves conflicted-file paths through the app's dedicated traversal/symlink guard, while a sibling function that operates on the exact same attacker-influenced `file.path` values reads the file with a naive `Path.join`, skipping that guard entirely.

### Finding Description
`buildConflictContext` in `app/src/lib/copilot-conflict-context.ts` explicitly guards every conflicted file path before touching disk: [1](#0-0) 

The comment is explicit about the intent: *"Guard against path traversal and symlink escapes (cross-platform)."* This uses `resolveWithin`, which normalizes the path, rejects null bytes, and — critically — calls `realpath()` on both the repository root and the resolved target so that a symlink planted inside the working tree that points outside the repo is detected and rejected: [2](#0-1) 

However, the same conflicted-file `path` value (taken from `WorkingDirectoryFileChange`/git status, i.e. content that lives inside a cloned/fetched repository and is therefore attacker-influenced when the repo is untrusted) is also consumed by `getResolutionDiff`, which is invoked directly from the Copilot conflicts UI (`copilot-conflicts-changes.tsx`) every time the user previews the "current"/"incoming"/"Copilot" resolution diff for a conflicted file: [3](#0-2) 

Here the working-tree ("base") content is read with `Path.join(repository.path, filePath)` — plain string joining with **no** call to `resolveWithin`, no `realpath` check, and no rejection of `..`-escaping or symlink-escaping paths. This is the exact same "one enforcement point is correct, the sibling enforcement point uses a different/weaker check on the same input" pattern as the Monad `CREATE`/`CREATE2` bug, where `create.hpp` hardcoded `0xC000` instead of deriving the limit from `monad_chain.cpp`'s `128 * 1024` config — except here the divergence is in a security guard rather than a numeric cap, so the consequence is a bypass rather than an availability regression.

### Impact Explanation
If a cloned/fetched repository contains, or a merge/rebase introduces, a working-tree entry whose reported "path" resolves (via a symlink component, or via `..`-style traversal that git's own protections failed to strip before the string reaches this code) outside the repository root, `buildConflictContext`'s guard would catch it and mark the file "path is outside the repository." But simply opening the Copilot conflict result dialog and viewing the diff for that same file calls `getResolutionDiff`, which reads `Path.join(repository.path, filePath)` directly — an out-of-repository file read. The read content (`baseContent`) is then written into a temp file and diffed, and displayed/streamed into the UI (and, via `buildFileContents`, into the syntax highlighter). This is an unprivileged, attacker-controlled-repo-triggered file read outside the repository boundary — the exact "file read outside the repo" impact category this task is scoped to.

### Likelihood Explanation
The trigger requires nothing beyond the user opening a repository containing a maliciously crafted working-tree conflict and viewing it in the Copilot conflict resolution dialog — a normal, expected user action, not a contrived multi-step scenario. The `resolveWithin` guard exists precisely because the authors recognized this class of risk for conflicted files (see the explicit comment in `copilot-conflict-context.ts`), which confirms the invariant is intended to hold universally for these paths; `getResolutionDiff` simply doesn't reuse it.

### Recommendation
Route `filePath` in `getResolutionDiff` through `resolveWithin(repository.path, filePath)` (the same helper used in `buildConflictContext` and in `app-store.ts`'s `_applyCopilotConflictResolutions`) before calling `readFile`, and treat a `null` result the same way those call sites do (skip/report rather than read). More generally, any function that reads a conflicted-file path supplied by working-directory status should funnel through a single shared "safe resolve" helper rather than re-implementing (or omitting) the check per call site.

### Proof of Concept
Exact reproduction was not verified end-to-end (would require constructing a working tree where git status reports a conflicted path that traverses outside the repo root via a symlink, which needs local repo manipulation to confirm in a running app), but the code-level divergence is directly demonstrable by comparing:
- `app/src/lib/copilot-conflict-context.ts:390-407` (guarded via `resolveWithin`)
- `app/src/lib/git/diff.ts:460-463` (unguarded `Path.join`)

for the identical `file.path` value flowing from the same conflicted-files list into both functions during a single Copilot conflict-resolution session (`copilot-conflicts-changes.tsx` calls both, per file, for the same conflict).

### Citations

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

**File:** app/src/lib/git/diff.ts (L447-463)
```typescript
export async function getResolutionDiff(
  repository: Repository,
  filePath: string,
  options: { content: string } | { stage: 'ours' | 'theirs' },
  hideWhitespaceInDiff: boolean = false
): Promise<IResolutionDiff> {
  const gitStage =
    'stage' in options ? (options.stage === 'ours' ? ':2' : ':3') : undefined

  // Always diff against the working-tree file (which still has conflict
  // markers). This gives a consistent baseline for all three resolution
  // choices (Copilot, current, incoming) so the user sees exactly what each
  // option changes relative to the file's current state on disk.
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```
