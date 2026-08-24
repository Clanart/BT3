### Title
Unbounded stdout/stderr buffering when shelling out to `git` for blob/diff content on `encoding: 'buffer'` allows a malicious repository to trigger out-of-memory Desktop crash - ([File: app/src/lib/git/core.ts])

### Summary
The core `git()` execution wrapper sets `maxBuffer: Infinity` for any call made with `encoding: 'buffer'`, meaning Node/dugite will buffer the entire stdout/stderr of the underlying `git` child process in memory with no upper bound before Desktop applies its own size checks. Several call sites that consume attacker-controlled repository content (blobs, diffs, log output) use this buffer encoding, so a repository crafted to make `git show`/`git diff` emit an extremely large amount of output can force Desktop to allocate unbounded memory, mirroring the Zebra CVE-2026-40881 pattern of allocating resources based on attacker-supplied size before enforcing a real limit.

### Finding Description
In `app/src/lib/git/core.ts`, the default execution options are: [1](#0-0) 

`maxBuffer` is only capped to `kStringMaxLength` when the caller requests string output; for `encoding: 'buffer'` it is explicitly set to `Infinity`, removing the built-in Node.js safety limit (`child_process.exec`'s `maxBuffer`) that would otherwise abort output collection once a threshold is exceeded (see `isMaxBufferExceededError`/`ERR_CHILD_PROCESS_STDIO_MAXBUFFER` handling a few lines above, which only fires when a finite `maxBuffer` is actually set) [2](#0-1) .

This buffer-mode path is used directly to read blob contents from a repository: [3](#0-2) 

`getBlobContents` has no `maxBuffer` override, so it inherits the `Infinity` default and will buffer the complete output of `git show <commitish>:<path>` for any blob in a checked-out or fetched repository, regardless of size. It is also used by `getResolutionDiff` and other diff/log/status readers with `encoding: 'buffer'`, all of which likewise rely on `Infinity` unless they separately pass a `maxBuffer`: [4](#0-3) 

Downstream, Desktop does have *content-size* sanity checks such as `isValidBuffer`/`isBufferTooLarge` (`MaxDiffBufferSize = 70e6`) [5](#0-4) , but these run only **after** the entire buffer has already been read into a Node `Buffer` in process memory — exactly the same "allocate-then-validate" ordering flaw described in the Zebra advisory (allocation happens before the size/limit check is applied). Unlike `getPartialBlobContentsCatchPathNotInRef`, which does pass an explicit `maxBuffer: length` and gracefully recovers from `isMaxBufferExceededError` [6](#0-5) , the plain `getBlobContents` function used for full-file/image diff rendering has no such bound.

### Impact Explanation
An attacker who controls the contents of a repository that the victim clones, fetches, or checks out (e.g., a large binary blob, an enormous single-line file, or pathological diff output) can cause GitHub Desktop to buffer an arbitrarily large amount of data into a single `Buffer` while merely displaying that file/diff/blob in the UI. Because Node process memory is finite, this can exhaust memory and crash the Desktop application (renderer/main process OOM), a Denial of Service consistent with "Allocation of Resources Without Limits or Throttling" (CWE-770), the same class as CVE-2026-40881. This does not require local/admin access — only that the victim opens or diffs a file from a maliciously crafted repository they cloned/fetched.

### Likelihood Explanation
Likelihood is moderate: normal usage would rarely produce multi-gigabyte git blob/diff output, but a specifically crafted malicious repository (a single huge tracked file, or a diff generating pathological output) is trivial for an attacker to construct and requires no unusual interaction beyond the victim opening/viewing the file in Desktop, which is a completely ordinary workflow.

### Recommendation
Set a concrete, bounded `maxBuffer` for all buffer-mode `git()` invocations (e.g., align with `MaxDiffBufferSize`/a similar cap) instead of `Infinity` in the default options in `app/src/lib/git/core.ts`, and have callers like `getBlobContents` handle `isMaxBufferExceededError` the same way `getPartialBlobContentsCatchPathNotInRef` already does, so the size check happens via the child-process buffer limit itself rather than after full in-memory materialization.

### Proof of Concept
1. Attacker creates a repository containing a single tracked file (or generates one via `git diff`) whose content substantially exceeds available system memory (e.g., several GB of repeated bytes), and hosts/shares it for the victim to clone or add as a remote.
2. Victim clones/fetches the repository in GitHub Desktop and opens the changed/diff view for that file (or any code path invoking `getBlobContents`/`getResolutionDiff` with `encoding: 'buffer'`).
3. `git(['show', ...], ..., { encoding: 'buffer' })` runs with `maxBuffer: Infinity` [1](#0-0) , so dugite/Node collects the entire multi-GB stdout into memory before Desktop's `isValidBuffer`/`MaxDiffBufferSize` check ever runs [7](#0-6) .
4. The unbounded allocation exhausts memory, crashing the Electron process (DoS).

I could not fully verify every call site that uses `encoding: 'buffer'` (some in `log.ts`/`status.ts` may pass their own `maxBuffer`), so the exact set of vulnerable code paths beyond `show.ts`/`diff.ts` should be confirmed with a full read of those files before remediation.

### Citations

**File:** app/src/lib/git/core.ts (L21-28)
```typescript
export const isMaxBufferExceededError = (
  error: unknown
): error is ExecError & { code: 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' } => {
  return (
    error instanceof ExecError &&
    error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER'
  )
}
```

**File:** app/src/lib/git/core.ts (L231-235)
```typescript
  const defaultOptions: IGitExecutionOptions = {
    successExitCodes: new Set([0]),
    expectedErrors: new Set(),
    maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength,
  }
```

**File:** app/src/lib/git/show.ts (L23-31)
```typescript
export const getBlobContents = (
  repository: Repository,
  commitish: string,
  path: string
) =>
  git(['show', `${commitish}:${path}`], repository.path, 'getBlobContents', {
    successExitCodes: new Set([0, 1]),
    encoding: 'buffer',
  }).then(r => r.stdout)
```

**File:** app/src/lib/git/show.ts (L69-88)
```typescript
export async function getPartialBlobContentsCatchPathNotInRef(
  repository: Repository,
  commitish: string,
  path: string,
  length: number
): Promise<Buffer | null> {
  const args = ['show', `${commitish}:${path}`]

  return git(args, repository.path, 'getPartialBlobContentsCatchPathNotInRef', {
    maxBuffer: length,
    expectedErrors: new Set([GitError.PathExistsButNotInRef]),
    encoding: 'buffer',
  })
    .then(r =>
      r.gitError === GitError.PathExistsButNotInRef ? null : r.stdout
    )
    .catch(e =>
      isMaxBufferExceededError(e) ? coerceToBuffer(e.stdout) : Promise.reject(e)
    )
}
```

**File:** app/src/lib/git/diff.ts (L42-76)
```typescript
/**
 * V8 has a limit on the size of string it can create (~256MB), and unless we want to
 * trigger an unhandled exception we need to do the encoding conversion by hand.
 *
 * This is a hard limit on how big a buffer can be and still be converted into
 * a string.
 */
const MaxDiffBufferSize = 70e6 // 70MB in decimal

/**
 * Where `MaxDiffBufferSize` is a hard limit, this is a suggested limit. Diffs
 * bigger than this _could_ be displayed but it might cause some slowness.
 */
const MaxReasonableDiffSize = MaxDiffBufferSize / 16 // ~4.375MB in decimal

/**
 * The longest line length we should try to display. If a diff has a line longer
 * than this, we probably shouldn't attempt it
 */
const MaxCharactersPerLine = 5000

/**
 * Utility function to check whether parsing this buffer is going to cause
 * issues at runtime.
 *
 * @param buffer A buffer of binary text from a spawned process
 */
function isValidBuffer(buffer: Buffer) {
  return buffer.length <= MaxDiffBufferSize
}

/** Is the buffer too large for us to reasonably represent? */
function isBufferTooLarge(buffer: Buffer) {
  return buffer.length >= MaxReasonableDiffSize
}
```

**File:** app/src/lib/git/diff.ts (L508-519)
```typescript
    const { stdout } = await git(args, repository.path, 'getResolutionDiff', {
      successExitCodes: new Set([0, 1]),
      encoding: 'buffer',
    })

    if (!isValidBuffer(stdout)) {
      return {
        diff: { kind: DiffType.Unrenderable },
        oldContents: baseContent,
        newContents: targetContent,
      }
    }
```
