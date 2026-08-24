### Title
`resolveWithin` uses a bare `String.startsWith` prefix check instead of a path-boundary check, allowing repository-escape writes/reads via sibling-directory names - (File: `app/src/lib/path.ts`)

### Summary
The path-containment guard used throughout Desktop to keep file operations inside a repository, `_resolveWithin` in [1](#0-0) , validates containment with `realResolved.startsWith(realRoot)`. This is a classic prefix-boundary bug: it does not require a path separator (or exact equality) after the prefix, so any resolved path whose string representation merely begins with the root's characters is accepted — even if it is a completely different, sibling directory (e.g. root `/Users/alice/Documents/myrepo` wrongly "contains" `/Users/alice/Documents/myrepo-secrets/…`). This mirrors the Sherlock report's root cause: a boundary comparison (`>` instead of `>=`) that fails to enforce the intended invariant and lets a forbidden action through at the exact boundary condition.

### Finding Description
`resolveWithin` is Desktop's single choke point for keeping file reads/writes inside a repository root. It is used to gate:
- Writing Copilot's merge-conflict resolution content to disk: `app/src/lib/stores/app-store.ts` `_acceptCopilotConflictResolutions`, calling `resolveWithin(repository.path, resolution.path)` then `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [2](#0-1) 
- Reading conflicted files for the Copilot prompt context, `buildConflictContext` [3](#0-2) 
- Opening a file from the `openRepo` deep-link's `filepath` parameter [4](#0-3) 

The containment decision itself is broken:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
There is no check that `realResolved === realRoot` or that the next character after `realRoot` is a path separator, so `myrepo-attacker-controlled` passes the check against root `myrepo`.

The most impactful reachable sink is the Copilot conflict-resolution write path. The `path` field written to disk originates from the LLM's JSON response, parsed in `parseCopilotConflictResolution` and only lightly normalized by `normalizeLLMPath`, which strips backslashes/`./`/duplicate slashes but does **not** reject `..` segments or otherwise canonicalize the value [5](#0-4) [6](#0-5) . The prompt fed to the model is built from repository-controlled data (commit messages, PR title/description, diff content) supplied by `gatherConflictResolutionContext`/`buildConflictContext`, i.e. content that originates from a cloned/fetched repository and can be adversarially crafted by whoever authored the conflicting branch/PR (classic LLM prompt-injection surface). If the model can be induced (via crafted commit messages/PR text embedded in a malicious repo) to emit a resolution `path` such as `../<siblingname>/…`, `resolveWithin` may accept it due to the prefix bug, and `_acceptCopilotConflictResolutions` will `writeFile` attacker-influenced content outside the intended repository, silently corrupting files the user will subsequently `git add`/commit/push.

### Impact Explanation
A successful escape lets Desktop write attacker-influenced file content to a directory outside the checked-out repository (e.g., a sibling checkout, backup folder, or `~/.config`-adjacent path that happens to share the repo folder name as a prefix), and the written path is then staged via `git add` and offered to the user to commit — i.e., silent corruption of what the user commits/pushes, and arbitrary file write outside the repo root. This matches the "file write ... outside the repo" and "silent corruption of what the user commits or pushes" categories in scope.

### Likelihood Explanation
Exploitation requires: (1) the attacker to control repository content that reaches the Copilot prompt (commit messages/PR description/diff — all attacker-controlled if they open a PR or push to a shared branch), (2) the model to be steered via prompt injection into emitting a crafted `path`, and (3) a sibling directory name that happens to share a prefix with the repository directory. Step (3) narrows real-world likelihood somewhat (it is not a universal `..`-only traversal), but it is a genuine structural defect in a security boundary used across multiple sinks, not merely theoretical — the existing unit tests for `resolveWithin` never test the "sibling directory shares a prefix" case, only `..`/symlink escapes [7](#0-6) , showing the gap is untested.

### Recommendation
Fix the containment check in `_resolveWithin` to require an exact match or a separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Additionally, treat LLM-supplied `path` values as untrusted: reject any `normalizeLLMPath` output containing `..` path segments or resolving outside the repo before ever reaching `resolveWithin`, and re-validate the final resolved path against the "add trailing separator" rule before calling `writeFile`.

### Proof of Concept
```ts
// app/src/lib/path.ts — _resolveWithin boundary bug
import { resolveWithin } from './path'

// repository root
const root = '/Users/alice/Documents/myrepo'
// a sibling directory that happens to exist alongside the repo,
// e.g. a backup or another checkout: /Users/alice/Documents/myrepo-secrets

// Copilot returns a conflict resolution with path: "../myrepo-secrets/config.json"
const resolved = await resolveWithin(root, '../myrepo-secrets/config.json')
// BUG: realResolved = "/Users/alice/Documents/myrepo-secrets/config.json"
// realRoot        = "/Users/alice/Documents/myrepo"
// "myrepo-secrets...".startsWith("myrepo") === true  -> resolved is NOT null
// even though myrepo-secrets is a completely different directory.

// app-store.ts _acceptCopilotConflictResolutions then does:
// await writeFile(resolved, resolution.resolvedContent, 'utf8')
// writing attacker-influenced content outside the repository root.
```

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L260-272)
```typescript
/**
 * Normalize a file path returned by the LLM. The model may return
 * Windows-style backslashes (`src\\file.ts`), a leading `./`, or redundant
 * separators — all of which would cause validation to reject an otherwise
 * correct resolution.
 */
function normalizeLLMPath(raw: string): string {
  return raw
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/\/\/+/g, '/')
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L388-395)
```typescript
    const obj = entry as Record<string, unknown>
    const { path, hunks: rawHunks, reasoning, action: rawAction } = obj

    if (typeof path !== 'string' || path.trim().length === 0) {
      throw new CopilotValidationError(
        `Copilot returned an invalid conflict resolution payload: "path" at index ${i} must be a non-empty string`
      )
    }
```

**File:** app/test/unit/path-test.ts (L44-101)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })

    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
    }
```
