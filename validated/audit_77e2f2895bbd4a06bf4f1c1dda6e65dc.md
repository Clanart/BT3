Confirmed. The vulnerability is real and unpatched at this location.

### Title
Symlink following in `getWorkingDirectoryImage` allows out-of-repo file disclosure via image diff - (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryImage` reads a working-directory file for image-diff rendering using a plain `Path.join(repository.path, file.path)` and `readFile`, without any symlink-safe resolution. Since `fs/promises.readFile` follows symlinks by default, a repository-tracked symlink whose target points outside the repository (e.g., to `~/.ssh/id_rsa` or any other file readable by the OS user) will have its target's bytes read, base64-encoded, and surfaced in the UI as an "image" diff.

### Finding Description [1](#0-0) 

`getWorkingDirectoryImage` builds the path via `Path.join(repository.path, file.path)` and passes it directly to `readFile`. The `file.path` for a `FileChange`/`WorkingDirectoryFileChange` comes straight from `git status --porcelain=2` parsing in `app/src/lib/status-parser.ts` (`parseChangedEntry`, `parsedRenamedOrCopiedEntry`) and `app/src/lib/git/status.ts` `buildStatusMap`, i.e., the literal repo-relative path git reports for a changed file — it will not itself contain `..` traversal, but nothing prevents that path from being (or containing) a symlink whose target resolves outside the repository. [2](#0-1) [3](#0-2) 

The codebase already has a sanctioned defense for exactly this class of issue: `resolveWithin` in `app/src/lib/path.ts`, which normalizes/joins path segments, then calls `realpath` on both the root and the resolved path and rejects (`return null`) if the resolved-real-path doesn't sit under the real root — this specifically catches symlink-based escapes, as validated by unit tests. [4](#0-3) [5](#0-4) 

This helper is already used elsewhere in the codebase for equivalent untrusted-repo-content-path scenarios, e.g. `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts` explicitly comments "Guard against path traversal and symlink escapes (cross-platform)" before reading conflict file contents, and `dispatcher.ts`'s `openRepositoryFromUrl` uses it before calling `shell.showItemInFolder`. [6](#0-5) [7](#0-6) 

`getWorkingDirectoryImage` has no equivalent check — it is the sink used when rendering the "current" side of an image diff for working-directory changes, ultimately populating an `Image` object (`IImageDiff.current`) that `Diff.renderImage` displays as a data-URI `<img>` in the UI.

### Impact Explanation
An attacker who controls a cloned/fetched repository can commit a symlink (e.g. `evil.png -> /home/user/.ssh/id_rsa` or any absolute/relative path escaping the repo) and then modify the underlying content indirectly (or simply have the symlink appear as untracked/modified so it shows up in the Changes list with an image-recognized extension). When the victim views that file's diff in the Changes pane, GitHub Desktop reads the *target* file's bytes via the followed symlink, base64-encodes them, and renders them as an image — disclosing arbitrary local file contents the OS user can read to the UI, and by extension to anything else that consumes that rendered diff (e.g., a screenshot, or if the app were to export/attach the diff elsewhere).

### Likelihood Explanation
Requires the victim to clone/fetch an attacker-controlled repository and open the Changes view for the malicious/modified symlinked file — no unusual user action beyond ordinary GitHub Desktop usage (viewing a diff of a changed file), which is the app's core workflow. This matches the described threat model: attacker controls cloned/fetched repository content, no local/privileged access needed.

### Recommendation
In `getWorkingDirectoryImage` (`app/src/lib/git/diff.ts:926-937`), resolve `file.path` against `repository.path` using the existing `resolveWithin` helper from `app/src/lib/path.ts` (as already done in `copilot-conflict-context.ts` and `dispatcher.ts`), and refuse to read (return an error/placeholder) if it resolves to `null`, before calling `readFile`.

### Proof of Concept
1. Create a repository, add a symlink `evil.png` pointing to `~/.ssh/id_rsa` (or any sensitive out-of-repo file), commit it.
2. Have the victim clone this repository with GitHub Desktop.
3. Modify the symlink target file content (or trigger any change that makes `evil.png` appear in the Changes list, e.g. re-target the symlink).
4. In GitHub Desktop, select `evil.png` in the Changes list to view its diff.
5. `getWorkingDirectoryImage` calls `readFile(Path.join(repository.path, 'evil.png'))`; because `evil.png` is a symlink, Node follows it to the sensitive target and the resulting bytes are base64-encoded and rendered as an image in the diff viewer, confirming disclosure of the out-of-repo file's content.

### Citations

**File:** app/src/lib/git/diff.ts (L926-937)
```typescript
export async function getWorkingDirectoryImage(
  repository: Repository,
  file: FileChange
): Promise<Image> {
  const contents = await readFile(Path.join(repository.path, file.path))
  return new Image(
    contents.buffer,
    contents.toString('base64'),
    getMediaType(Path.extname(file.path)),
    contents.length
  )
}
```

**File:** app/src/lib/status-parser.ts (L101-119)
```typescript
// 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
const changedEntryRe =
  /^1 ([MADRCUTX?!.]{2}) (N\.\.\.|S[C.][M.][U.]) (\d+) (\d+) (\d+) ([a-f0-9]+) ([a-f0-9]+) ([\s\S]*?)$/

function parseChangedEntry(field: string): IStatusEntry {
  const match = changedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseChangedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for changed entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[8],
  }
}
```

**File:** app/src/lib/git/status.ts (L297-349)
```typescript
function buildStatusMap(
  files: Map<string, WorkingDirectoryFileChange>,
  entry: IStatusEntry,
  conflictDetails: ConflictFilesDetails
): Map<string, WorkingDirectoryFileChange> {
  const status = mapStatus(
    entry.statusCode,
    entry.submoduleStatusCode,
    entry.renameOrCopyScore
  )

  if (status.kind === 'ordinary') {
    // when a file is added in the index but then removed in the working
    // directory, the file won't be part of the commit, so we can skip
    // displaying this entry in the changes list
    if (
      status.index === GitStatusEntry.Added &&
      status.workingTree === GitStatusEntry.Deleted
    ) {
      return files
    }
  }

  if (status.kind === 'untracked') {
    // when a delete has been staged, but an untracked file exists with the
    // same path, we should ensure that we only draw one entry in the
    // changes list - see if an entry already exists for this path and
    // remove it if found
    files.delete(entry.path)
  }

  // for now we just poke at the existing summary
  const appStatus = convertToAppStatus(
    entry.path,
    status,
    conflictDetails,
    entry.oldPath
  )

  const initialSelectionType =
    appStatus.kind === AppFileStatusKind.Modified &&
    appStatus.submoduleStatus !== undefined &&
    !appStatus.submoduleStatus.commitChanged
      ? DiffSelectionType.None
      : DiffSelectionType.All

  const selection = DiffSelection.fromInitialSelection(initialSelectionType)

  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
  return files
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
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
```
