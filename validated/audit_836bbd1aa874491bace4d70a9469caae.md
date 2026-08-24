## Finding: Copilot conflict-resolution paths are not restricted to the conflicted-file set, and `resolveWithin` does not exclude `.git`

### Title
LLM-controlled resolution paths in `_applyCopilotConflictResolutions` can target `.git/hooks/*` for arbitrary file write - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The Solidity report's broken invariant is: an item that should be excluded from a second, less-guarded processing path is not actually excluded, so it gets acted on twice/unexpectedly, corrupting the operation. The closest structural analog in Desktop is the Copilot merge-conflict auto-resolution pipeline: conflict content from an attacker-controlled repository is fed to an LLM, and the LLM's JSON response (`copilotResolutions`) is trusted to name a `path` and `resolvedContent` that get written to disk and staged, with only a "is this inside the repo root" check — not a check that the path is one of the actual conflicted files, nor that it stays outside `.git`.

### Finding Description
`_applyCopilotConflictResolutions` iterates `copilotResolutions` (parsed from a model response) and, for each entry not covered by a manual override, calls: [1](#0-0) 

The only safety check on `resolution.path` is `resolveWithin(repository.path, resolution.path)`: [2](#0-1) 

`resolveWithin` guarantees the resolved path (after `realpath`) is at or under `repository.path` — but `.git` is itself a subdirectory of `repository.path`, so a path like `.git/hooks/pre-commit` legitimately passes this check. There is no additional guard rejecting paths inside `.git`, and no verification that `resolution.path` matches one of the file paths that were actually sent to the model as conflicted files (i.e., the trusted `IFileConflictContext.path` set built in `buildConflictContext`): [3](#0-2) 

Because conflict hunk content (attacker-controlled `theirs`/`ours` text from a malicious branch/PR) is sent verbatim into the LLM prompt, a classic prompt-injection payload embedded in a conflicting file could attempt to manipulate the model into emitting an extra/different resolution object whose `path` is `.git/hooks/pre-commit` (or `post-commit`, `post-checkout`, etc.) with attacker-chosen shell script content. If that succeeds, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` writes the hook file unconditionally, and the subsequent `git add -- ...` / later `git commit`/`git rebase --continue`/`git merge --continue` calls (which do not appear to sandbox hook execution — `interceptHooks` in `createCommit` intercepts output/progress, it does not disable hook execution) would execute that script as the user. [4](#0-3) 

This mirrors the audit's core problem: a value that should have been filtered out by an earlier, narrower list (the actual conflicted files / repo working tree, analogous to "blocked adapters") is instead accepted by a second, more permissive check (`resolveWithin`, analogous to the unfiltered `_adapter_states` loop), producing a write that shouldn't be possible.

### Impact Explanation
If the path-restriction gap is real (unconfirmed — see Likelihood), this allows an attacker who only controls repository/PR content merged/rebased by the victim to achieve local code execution via git hooks, which is one of the explicitly in-scope impacts (code execution / silent corruption of what the user commits, via a git remote/repo the attacker controls).

### Likelihood Explanation
This is **not fully confirmed** from the code I was able to inspect. Two open questions bound on likelihood that I could not resolve with the available tools:
1. Whether `parseCopilotConflictResolution` (in `app/src/lib/copilot-conflict-resolution.ts`) validates/whitelists `resolution.path` against the actual set of conflicted files (`ICopilotConflictContext.files[].path`) before it reaches `_applyCopilotConflictResolutions`. If it does, this path is not exploitable.
2. Whether prompt injection via conflict-hunk content can reliably steer the LLM into emitting a resolution for a path outside the intended file set at all, given the prompt structure in `formatConflictContextForPrompt` explicitly enumerates only real conflicted paths.

Given these unresolved points, I cannot state this as a confirmed vulnerability with full confidence — the index does not give me visibility into the full `parseCopilotConflictResolution` implementation logic (only test file references were found).

### Recommendation
- In `_applyCopilotConflictResolutions`, validate `resolution.path` against the known set of conflicted file paths (the same list passed into `buildConflictContext`) before writing/staging, rejecting any path not in that set — do not rely solely on `resolveWithin`.
- Additionally harden `resolveWithin`/the caller to explicitly reject any resolved path whose relative path starts with `.git` (or resolves under the repository's git directory as reported by `git rev-parse --git-dir`), independent of the conflicted-file-set check, as defense in depth.

### Proof of Concept
Not independently verified end-to-end due to the unresolved parsing/validation question above. Conceptually:
1. Attacker opens a PR/branch whose conflicting file content contains a prompt-injection payload instructing the assistant to also "resolve" a file at `.git/hooks/post-commit` with a malicious script.
2. Victim uses Desktop's Copilot conflict-resolution flow to resolve conflicts during a merge/rebase/cherry-pick with this branch.
3. If the model responds with a resolution object for `.git/hooks/post-commit` and that object is not filtered out, `_applyCopilotConflictResolutions` writes it to disk (`resolveWithin` permits it since `.git` is under `repository.path`) and the next commit runs the attacker's hook script.

Given the confirmation gaps noted above, treat this as a **candidate** finding requiring further verification of `app/src/lib/copilot-conflict-resolution.ts`'s `parseCopilotConflictResolution` rather than a fully proven vulnerability.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
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

**File:** app/src/lib/copilot-conflict-context.ts (L367-401)
```typescript
export async function buildConflictContext(
  ourLabel: string,
  theirLabel: string,
  workingDirectory: string,
  files: ReadonlyArray<{
    readonly path: string
    /** Which side deleted the file (for delete-vs-modify conflicts). */
    readonly deletedSide?: 'ours' | 'theirs'
  }>
): Promise<ICopilotConflictContext> {
  const results = await Promise.all(
    files.map(async (file): Promise<IFileConflictContext> => {
      // Delete-vs-modify conflicts have no text markers on disk. Include
      // them in the context with metadata so the model can recommend
      // keep or delete — no file content is needed.
      if (file.deletedSide !== undefined) {
        return {
          path: file.path,
          hunks: [],
          deleteConflict: { deletedSide: file.deletedSide },
        }
      }

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
```

**File:** app/src/lib/git/commit.ts (L51-70)
```typescript
  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
```
