This is a legitimate finding: `getNewFileContent` never runs the resolved path through the `resolveWithin`/`resolveWithinPosix` symlink-containment check that exists elsewhere in the codebase, and `readPartialFile` relies on `fs.createReadStream`, which follows symlinks.

### Title
Symlink escape reads arbitrary local files into the diff/syntax-highlighting pipeline - (File: `app/src/ui/diff/syntax-highlighting/index.ts`)

### Summary
`getNewFileContent` builds the on-disk path for a `WorkingDirectoryFileChange` with a raw `Path.join(repository.path, file.path)` and passes it straight to `readPartialFile`, which opens it with `fs.createReadStream` [1](#0-0) . `fs.createReadStream`/`createReadStream` does not use `O_NOFOLLOW`, so if `file.path` (or an intermediate path component) is a symlink whose target resolves outside `repository.path`, the stream will transparently follow it and read the external target's bytes [2](#0-1) . Those bytes are then decoded as UTF-8, split into lines, and handed to `getFileContents`/the CodeMirror highlighter worker for rendering in the diff view [3](#0-2) .

The codebase already has a purpose-built defense for exactly this class of bug — `resolveWithin`/`resolveWithinPosix` in `app/src/lib/path.ts`, which `realpath`s both the root and the resolved path and rejects anything whose real path escapes the root, explicitly covering the "symlink used to traverse outside the root" case (there's even a unit test for it) [4](#0-3) [5](#0-4) . That helper is used in other call sites such as `dispatcher.ts` and `copilot-conflict-context.ts`, but `getNewFileContent` in the syntax-highlighting module does not call it before reading the file [6](#0-5) .

### Impact Explanation
If exploited, arbitrary local files readable by the Desktop process (subject to filesystem permissions of the logged-in user, e.g. SSH keys, cloud credential files, other repos) can have their contents surfaced inside the rendered diff/syntax-highlighted view. This matches the "read outside the repo" impact category in scope. The information is only displayed in the UI (not exfiltrated over the network by this code path alone), but a user who screenshots/copies the diff, or an extension/automation that scrapes the rendered DOM, could leak it further.

### Likelihood Explanation
The main constraint is getting a symlink escaping the repo boundary to appear as a `WorkingDirectoryFileChange` (i.e. as an uncommitted/pending change) rather than as a `CommittedFileChange` (which instead goes through `getPartialBlobContents`/`git show`, reading the blob's stored symlink-target string, not the dereferenced target — that path is safe). A normal `git clone` of a malicious repo alone will not produce a pending change unless the working tree subsequently diverges from the index/HEAD. Plausible attacker-controlled ways this divergence can occur without extra local privileges include: a merge/rebase conflict where one side replaces a regular file with a symlink (a git "typechange"), or a checked-out PR branch containing a symlink that legitimately shows as newly added/modified relative to the base being compared — both are ordinary Desktop workflows (fetch, merge, checkout branch/PR) driven entirely by attacker-controlled repository content. This makes the likelihood non-trivial but conditional on reaching one of these git states; I could not confirm from the indexed code whether any other layer (e.g. status parsing, `mapStatus`) filters out symlink typechange entries before they reach the diff view.

### Recommendation
In `getNewFileContent` (and any other place in the working-directory diff/highlight path that turns `WorkingDirectoryFileChange.path` into an on-disk path for reading), resolve the path with `resolveWithin(repository.path, file.path)` (or `resolveWithinPosix`, consistent with usage elsewhere in the codebase) and refuse to read if it returns `null`. Alternatively/additionally, `readPartialFile` could `lstat`/`realpath` the target first and reject non-regular files or paths whose real path is outside an expected root, rather than relying on callers to have already sanitized the path.

### Proof of Concept
1. Create a test repository `victim-repo`.
2. Outside the repo, create a sensitive file, e.g. `/tmp/secret.txt` containing `SECRET-DATA`.
3. Inside `victim-repo`, create a symlink `leak -> /tmp/secret.txt` and get it into a state where it is a `WorkingDirectoryFileChange` in Desktop (e.g. via a merge that leaves `leak` as a locally-modified/typechanged entry versus HEAD, or by committing a placeholder for `leak` and then swapping it locally to reproduce the "modified" state used by the automated test harness).
4. Call `getFileContents(repository, fileChangeForLeak)` (as `SeamlessDiffSwitcher`/`getNewFileContent` does) and observe that `newContents` in the returned `IFileContents` contains `SECRET-DATA` from `/tmp/secret.txt`, confirming that `readPartialFile`'s `fs.createReadStream(Path.join(repository.path, 'leak'), ...)` followed the symlink outside the repository boundary. [3](#0-2) [2](#0-1)

### Citations

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L81-105)
```typescript
async function getNewFileContent(
  repository: Repository,
  file: ChangedFile
): Promise<Buffer | null> {
  if (file.status.kind === AppFileStatusKind.Deleted) {
    return null
  }

  if (file instanceof WorkingDirectoryFileChange) {
    return readPartialFile(
      Path.join(repository.path, file.path),
      0,
      MaxHighlightContentLength - 1
    )
  } else if (file instanceof CommittedFileChange) {
    return getPartialBlobContents(
      repository,
      file.commitish,
      file.path,
      MaxHighlightContentLength
    )
  }

  return assertNever(file, 'Unknown file change type')
}
```

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L107-130)
```typescript
export async function getFileContents(
  repo: Repository,
  file: ChangedFile
): Promise<IFileContents> {
  const [oldContents, newContents] = await Promise.all([
    getOldFileContent(repo, file).catch(e => {
      log.error('Could not load old contents for syntax highlighting', e)
      return null
    }),
    getNewFileContent(repo, file).catch(e => {
      log.error('Could not load new contents for syntax highlighting', e)
      return null
    }),
  ])

  return {
    file,
    oldContents: oldContents?.toString('utf8').split(/\r?\n/) ?? [],
    newContents: newContents?.toString('utf8').split(/\r?\n/) ?? [],
    canBeExpanded:
      newContents !== null &&
      newContents.length <= MaxDiffExpansionNewContentLength,
  }
}
```

**File:** app/src/lib/file-system.ts (L61-78)
```typescript
export async function readPartialFile(
  path: string,
  start: number,
  end: number
): Promise<Buffer> {
  return await new Promise<Buffer>((resolve, reject) => {
    const chunks = new Array<Buffer>()
    let total = 0

    createReadStream(path, { start, end })
      .on('data', (chunk: Buffer) => {
        chunks.push(chunk)
        total += chunk.length
      })
      .on('error', reject)
      .on('end', () => resolve(Buffer.concat(chunks, total)))
  })
}
```

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/path-test.ts (L65-78)
```typescript
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
```
