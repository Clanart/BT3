Based on the code I found, I can present a real analog in `app/src/lib/path.ts`.

### Title
Path-containment check in `resolveWithin` uses substring comparison instead of path-boundary comparison, allowing writes/reads to escape the repository root - (File: `app/src/lib/path.ts`)

### Summary
The HyVM report's core issue is that `FIX_MEMOFFSET` computes a memory offset with an unchecked `add`, so the "is this address inside the protected region" invariant silently breaks and an attacker-controlled offset can land outside the intended boundary. `app/src/lib/path.ts` implements the exact analog for filesystem boundaries in GitHub Desktop: `resolveWithin` is the function every repo-scoped file read/write path relies on to guarantee "this path stays inside the repository," and its containment check has the same class of flaw — it uses a raw string-prefix comparison rather than a path-segment-boundary comparison.

### Finding Description
`_resolveWithin` computes the canonical root and resolved path and then decides containment with: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` has no concept of a path separator boundary. If `realRoot` is `/Users/victim/repos/proj`, then any path whose canonical form is `/Users/victim/repos/proj-something/...` also satisfies `startsWith(realRoot)`, because `"proj-something"` textually begins with `"proj"`. The function never appends a trailing separator to `realRoot` before comparing, so a sibling directory whose name happens to extend the repository directory's name as a string is incorrectly treated as "inside" the repository.

This mirrors the HyVM bug precisely: the guard exists (`FIX_MEMOFFSET` shifts the offset; `resolveWithin` canonicalizes and compares), but the boundary check itself is not exact, so a value that should be rejected as "outside the protected region" passes.

`resolveWithin` is the trust boundary used by attacker-influenced write paths, notably the Copilot conflict-resolution file writer, which resolves each conflicted file's repo-relative path before writing model-generated content to disk: [2](#0-1) 

and the conflict-context reader that loads file contents to send to the model: [3](#0-2) 

Both treat `resolveWithin(repository.path, file.path) !== null` as proof that the path is safely inside the repository, and both `file.path` values originate from `git status`/conflict metadata, which is data that a hostile repository can shape (e.g., via crafted tree entries, renames, or conflict markers surfaced during a merge/rebase of attacker-controlled content).

### Impact Explanation
If the resolved absolute path lands in a directory that is a false-positive match under the substring check (a sibling directory whose name has the repository's directory name as a prefix), Desktop will write attacker-influenced content (the Copilot-resolved file content) to a location it believes is safely contained in the repository, when it is actually outside it. Depending on what exists at that sibling location, this can silently corrupt files the user did not intend to touch, or write content into a directory that gets picked up by another workflow (e.g., a git worktree directory adjacent to the main repo, which Desktop and git conventionally name as `<repo>-<suffix>`). This is a "silent corruption of what the user commits" class impact called out as in-scope, because the write bypasses the intended repo boundary while `resolveWithin` reports success.

### Likelihood Explanation
Exploitability is conditional: the attacker needs a crafted repo-relative path (achievable, since these come from git-reported statuses/paths that are influenced by branch/tree content) and a pre-existing sibling directory on disk whose name textually extends the repository folder's name as a prefix. This precondition is realistic in ordinary Desktop usage because git worktrees are conventionally created as sibling directories named `<repo>-<branch>` next to the main repository — a very common naming pattern for anyone using worktrees with Desktop. This makes the flaw a plausible, if not universal, real-world condition rather than a purely theoretical one. I was not able to fully verify the exact directory-naming convention Desktop's worktree feature uses (`app/src/lib/git/worktree.ts`, `app/src/ui/worktrees/add-worktree-dialog.tsx`) within the scope of this investigation, so this precondition should be confirmed before treating likelihood as high.

### Recommendation
- **Short term:** Fix the containment check in `_resolveWithin` (`app/src/lib/path.ts:71`) to compare on path-segment boundaries instead of raw substrings, e.g. `realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)` (using the platform-appropriate separator for the `options` passed in).
- **Long term:** Add fuzz/property tests to `app/test/unit/path-test.ts` that specifically construct sibling directories whose names are prefixes/extensions of the root directory name, to catch this class of boundary-check regression going forward (analogous to the report's recommendation to fuzz `FIX_MEMOFFSET`).

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdir } from 'fs/promises'
import * as Path from 'path'

// Setup: two sibling directories where one's name is a prefix of the other
// /tmp/test/proj              <- the "repository"
// /tmp/test/proj-worktree     <- a sibling, e.g. a git worktree dir
await mkdir('/tmp/test/proj', { recursive: true })
await mkdir('/tmp/test/proj-worktree', { recursive: true })

// Attacker-influenced relative path (e.g. from a crafted git status entry)
// crosses out of proj into the sibling directory
const result = await resolveWithin('/tmp/test/proj', '../proj-worktree/payload.txt')

// BUG: result is NOT null even though the resolved path is outside
// "/tmp/test/proj" — it incorrectly passes the containment check because
// "/tmp/test/proj-worktree".startsWith("/tmp/test/proj") is true.
console.log(result) // "/tmp/test/proj-worktree/payload.txt" (should be null)
```

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/lib/stores/app-store.ts (L7233-7258)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-408)
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
