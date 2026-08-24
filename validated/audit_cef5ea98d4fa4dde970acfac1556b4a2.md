### Title
Unbounded `maxBuffer` for `encoding: 'buffer'` git invocations allows attacker-controlled repository content to force unmetered memory growth - ([File: app/src/lib/git/core.ts])

### Summary
The report's root cause is a resource that is metered for every normal path except one — an attacker-controlled input path (`call_result_write`) that skips `apply_gas_cost`, letting untrusted content dictate unbounded memory/CPU consumption. The Desktop analog is the git process execution wrapper `git()` in `app/src/lib/git/core.ts`, which sets `maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength` [1](#0-0) . Every caller that requests `encoding: 'buffer'` (blob reads, diffs, `git show`, status, log) is thus exempted from the buffer cap that string-encoded calls get, meaning Node/dugite will read the entire child-process stdout into memory with no upper bound before any application-level size check runs.

### Finding Description
`git()` builds `defaultOptions` where the `maxBuffer` passed to dugite's `exec` (which wraps Node's `child_process.execFile`/spawn buffering) is `Infinity` whenever the caller uses `encoding: 'buffer'` [1](#0-0) . This is analogous to the missing `apply_gas_cost` call: string-mode invocations are "metered" via `kStringMaxLength`, but buffer-mode invocations have that metering explicitly removed.

Multiple callers that operate on attacker-influenced repository content use `encoding: 'buffer'` without further capping `maxBuffer` themselves, e.g.:
- `getBlobContents` in `app/src/lib/git/show.ts` reads a full blob (`git show <commitish>:<path>`) with `encoding: 'buffer'` and no `maxBuffer` override, so it inherits `Infinity` [2](#0-1) .
- Diff retrieval paths in `app/src/lib/git/diff.ts` invoke `git(args, ..., { encoding: 'buffer' })` and only apply size checks (`isValidBuffer`, `MaxDiffBufferSize`) *after* the entire buffer has already been fully materialized in process memory [3](#0-2) .

By contrast, `getPartialBlobContentsCatchPathNotInRef` explicitly demonstrates that the codebase is aware buffer reads must be capped — it passes `maxBuffer: length` [4](#0-3)  — showing the `Infinity` default in `core.ts` is a deliberate but unsafe fallback for every other buffer-mode caller.

A repository controlled or influenced by an attacker (a cloned/fetched repo, a malicious remote's ref, or a crafted blob committed by a collaborator) can contain a single blob or diff of attacker-chosen size (limited only by disk/transport, i.e. effectively unbounded — gigabytes). When Desktop runs any `encoding: 'buffer'` git command against it (viewing a diff, viewing history, generating an image diff, computing changeset data), the entire output is buffered into a Node `Buffer` in the renderer/main process before any of the application's own size guards (`isValidBuffer`, `MaxDiffBufferSize`, etc.) get a chance to reject it. The existing guards in `diff.ts` are all post-hoc: they check the buffer size after Node has already allocated and populated it.

### Impact Explanation
This mirrors the Sherlock H-14 impact category directly: an unprivileged, attacker-supplied artifact (here, a Git blob/diff instead of a Wasm call result) drives unmetered memory growth in the host application. On a victim's machine, opening or diffing a maliciously large file/blob can exhaust available memory, causing the Desktop renderer/main process to crash or the OS to become unresponsive (out-of-memory) — a resource-exhaustion condition rooted in a genuine missing-guard defect in `core.ts`, not merely "large diffs are slow." Because `maxBuffer` is the *only* mechanism Node provides to bound child-process output before it's fully read, removing it (setting `Infinity`) removes the sole hard backstop; all in-app checks (`MaxDiffBufferSize`, `isBufferTooLarge`) execute strictly after the unbounded allocation has already occurred.

### Likelihood Explanation
Any repository a user clones, fetches from, or opens (including one shared by a collaborator or pulled from a compromised/malicious remote) can contain an oversized blob or generate an oversized diff; no special permissions, admin rights, or local access are required — only that the user perform an ordinary Desktop action (view diff/history/blob) against attacker-influenced content, consistent with the "Valid Impact" scope (attacker controls a cloned/fetched repository). The `getPartialBlobContentsCatchPathNotInRef` function already models a safe pattern (`maxBuffer: length`), indicating the fix is straightforward and consistent with the codebase's own conventions.

### Recommendation
In `app/src/lib/git/core.ts`, do not default `maxBuffer` to `Infinity` for `encoding: 'buffer'` calls. Instead, set a finite, application-defined ceiling (e.g. the existing `MaxDiffBufferSize`/`kStringMaxLength`-equivalent constant, or let each caller pass an explicit bound similar to `getPartialBlobContentsCatchPathNotInRef`), and handle `isMaxBufferExceededError` gracefully (the infrastructure for this — `isMaxBufferExceededError`, `ExecError` — already exists and is used elsewhere) [5](#0-4) .

### Proof of Concept
1. Attacker publishes/pushes a repository (or a branch/commit reachable via a remote the victim adds) containing a single blob of several GB (or a text file producing a multi-GB diff against an empty tree).
2. Victim clones/fetches the repository in GitHub Desktop and opens the file's diff or attempts to view its contents (`getBlobContents` in `app/src/lib/git/show.ts`, or the diff path in `app/src/lib/git/diff.ts`).
3. `git()` in `app/src/lib/git/core.ts` invokes the underlying git process with `maxBuffer: Infinity` because `encoding: 'buffer'` was requested [1](#0-0) .
4. Node/dugite buffers the entire multi-GB stdout into memory before returning; only after that does `diff.ts`'s `isValidBuffer`/`MaxDiffBufferSize` check run [3](#0-2) , by which point the unbounded allocation has already occurred, exhausting memory and potentially crashing the Desktop process.

Note: I was unable to fully trace every downstream consumer of `encoding: 'buffer'` (e.g. `log.ts`, `status.ts`) within the available context to confirm whether any of them apply their own `maxBuffer` override before hitting `core.ts`'s default; the two most representative call sites (`show.ts`, `diff.ts`) confirm the pattern, but a complete audit of all six matches would benefit from a full Devin session with file-content access.

### Citations

**File:** app/src/lib/git/core.ts (L231-235)
```typescript
  const defaultOptions: IGitExecutionOptions = {
    successExitCodes: new Set([0]),
    expectedErrors: new Set(),
    maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength,
  }
```

**File:** app/src/lib/git/core.ts (L296-317)
```typescript
          ).catch(err => {
            // If this is an exception thrown by Node.js (as opposed to
            // dugite) let's keep the salient details but include the name of
            // the operation.
            if (isErrnoException(err)) {
              throw new Error(`Failed to execute ${name}: ${err.code}`)
            }

            if (isMaxBufferExceededError(err)) {
              throw new ExecError(
                `${err.message} for ${name}`,
                err.stdout,
                err.stderr,
                // Dugite stores the original Node error in the cause property, by
                // passing that along we ensure that all we're doing here is
                // changing the error message (and capping the stack but that's
                // okay since we know exactly where this error is coming from).
                // The null coalescing here is a safety net in case dugite's
                // behavior changes from underneath us.
                err.cause ?? err
              )
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

**File:** app/src/lib/git/show.ts (L69-81)
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
```

**File:** app/src/lib/git/diff.ts (L69-76)
```typescript
function isValidBuffer(buffer: Buffer) {
  return buffer.length <= MaxDiffBufferSize
}

/** Is the buffer too large for us to reasonably represent? */
function isBufferTooLarge(buffer: Buffer) {
  return buffer.length >= MaxReasonableDiffSize
}
```
