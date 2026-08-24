## Analysis

The mechanics proposed in the question (`MaxDiffExpansionNewContentLength` passing for a *large* off-repo file while `readPartialFile` dereferences a symlink) don't quite line up technically, but the underlying concern — off-repo content disclosure through symlink dereferencing in the diff-expansion path — is real for files within (or truncated to) the byte-range limit.

`readPartialFile` reads via Node's `createReadStream(path, { start, end })`, which follows symlinks by default (no `lstat`/no-follow flag is used): [1](#0-0) 

`getNewFileContent` builds the on-disk path by joining `repository.path` with the changed file's repo-relative path and passes it straight to `readPartialFile`, with **no containment check** on the resolved (symlink-followed) target: [2](#0-1) 

That's notable because the codebase already has a purpose-built utility, `resolveWithin`, that specifically defends against exactly this symlink-escape pattern (validated by unit tests exercising a symlink that "traverses outside of the root"): [3](#0-2) 
`resolveWithin` is used elsewhere (`app/src/lib/stores/app-store.ts`, `app/src/lib/copilot-conflict-context.ts`, `app/src/ui/dispatcher/dispatcher.ts`) but is **not** applied before `readPartialFile` in `getNewFileContent`.

Regarding the specific `MaxDiffExpansionNewContentLength` mechanic: because the read is range-bounded (`0` to `MaxHighlightContentLength - 1`), `readPartialFile` can never return more than `MaxHighlightContentLength` (1,048,576) bytes regardless of the real target size: [4](#0-3) 
So `canBeExpanded` is computed correctly as a truncation signal — if the off-repo symlink target is ≥1,048,576 bytes, the read returns exactly that many bytes, `canBeExpanded` becomes `false`, and expansion is blocked: [5](#0-4) 
This means the check is **not actually bypassed** for arbitrarily large files as hypothesized — the attacker's off-repo target must be ≤ ~1MB for `canBeExpanded` to be `true`. That's a correction to the premise, but it isn't a meaningful defense: 1MB is more than enough to exfiltrate typical sensitive files (SSH keys, config files, source code, tokens) via the "Show more" expansion, since `expandTextDiffHunk`/`expandWholeTextDiff` splice `newContentLines` (sourced from the dereferenced off-repo file) directly into the rendered diff hunk: [6](#0-5) 

Git itself does not dereference symlinks when diffing (a tracked symlink's blob content is the target path string), so the initial git-produced diff for a changed symlink would normally show only a one-line target-string change. But `getNewFileContent`'s independent, symlink-following filesystem read supplies the "new content" used for syntax highlighting and — critically — for diff-expansion context lines, creating a mismatch where expansion pulls in dereferenced off-repo file bytes that were never part of git's diff output.

### Title
Symlink dereference in working-directory diff/expansion content read discloses off-repo file contents - (File: app/src/ui/diff/syntax-highlighting/index.ts)

### Summary
`getNewFileContent` reads working-directory file content for diff syntax highlighting and hunk expansion by joining `repository.path` with the file's repo-relative path and calling `readPartialFile`, which uses `fs.createReadStream` and therefore follows symlinks. If the changed path in the working directory is a symlink pointing outside the repository, the function reads and returns up to ~1MB of the **target's** content rather than repository-scoped content, and that content is subsequently spliced into the rendered diff via `expandTextDiffHunk`/`expandWholeTextDiff` when the user expands hunk context.

### Finding Description
`getNewFileContent` computes the path as `Path.join(repository.path, file.path)` and calls `readPartialFile` without ever validating that the resolved (symlink-followed) real path stays under `repository.path`, unlike the codebase's own `resolveWithin` helper which exists specifically to reject such symlink escapes. `readPartialFile` uses `createReadStream`, which dereferences symlinks. `canBeExpanded` only gates on the *byte length* actually read (bounded to `MaxHighlightContentLength`), not on where the bytes came from, so it does not detect or block an off-repo symlink target as long as that target is smaller than ~1MB.

### Impact Explanation
An attacker who controls repository content can commit a symlink (e.g., pointing at `../../../.ssh/id_rsa`, another checked-out repo, or any file reachable via a relative/absolute path) that is later shown as a working-directory change. Opening the diff, and especially clicking "Show more" to expand context, causes Desktop to read and display the dereferenced target's content — disclosing local file data that is outside the selected repository's scope to the UI (and, since `getFileContents`/`highlightContents` are shared utilities, potentially into related surfaces such as the Copilot/merge-conflict content flows that reuse this file-reading path).

### Likelihood Explanation
Requires only that the victim clone/open a malicious repository containing a symlinked working-directory entry and view/expand its diff — no special user privileges, admin rights, or pre-existing local compromise needed. The target file must be ≤ ~1MB for full expansion to be permitted by `canBeExpanded`, which covers most sensitive small files (keys, tokens, configs).

### Recommendation
Before calling `readPartialFile` in `getNewFileContent`, resolve the real path and use the existing `resolveWithin(repository.path, file.path)` (or equivalent `fs.realpath`/`lstat` check) to ensure the resolved path is contained within the repository; if not, treat as null/inaccessible content (matching how `AppFileStatusKind.Deleted` is already treated).

### Proof of Concept
1. Create a repository, add and commit a symlink `leak -> /etc/passwd` (or an absolute/relative path to a sensitive local file under ~1MB).
2. Modify the symlink target slightly to appear as a working-directory change in Desktop's Changes list.
3. Open the diff for `leak` in Desktop; observe `getFileContents`/`getNewFileContent` reads `/etc/passwd`'s actual content (not the symlink target string) for syntax highlighting.
4. Click "Show more"/expand on the resulting hunk; confirm the expanded context lines shown originate from `/etc/passwd`'s content rather than any repository-scoped blob, verifiable via a unit test around `expandTextDiffHunk`/`getFileContents` using a symlinked fixture file pointing outside a temp repo root, analogous to the existing symlink tests in `app/test/unit/path-test.ts`.

### Citations

**File:** app/src/lib/file-system.ts (L61-77)
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
```

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L19-27)
```typescript
/** The maximum number of bytes we'll process for highlighting. */
const MaxHighlightContentLength = 1024 * 1024

// There is no good way to get the actual length of the old/new contents,
// since we're directly truncating the git output to up to MaxHighlightContentLength
// characters. Therefore, when we try to limit diff expansion, we can't know if
// a file is exactly MaxHighlightContentLength characters long or longer, so
// we'll look for exactly that amount of characters minus 1.
export const MaxDiffExpansionNewContentLength = MaxHighlightContentLength - 1
```

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L89-94)
```typescript
  if (file instanceof WorkingDirectoryFileChange) {
    return readPartialFile(
      Path.join(repository.path, file.path),
      0,
      MaxHighlightContentLength - 1
    )
```

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L122-129)
```typescript
  return {
    file,
    oldContents: oldContents?.toString('utf8').split(/\r?\n/) ?? [],
    newContents: newContents?.toString('utf8').split(/\r?\n/) ?? [],
    canBeExpanded:
      newContents !== null &&
      newContents.length <= MaxDiffExpansionNewContentLength,
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

**File:** app/src/ui/diff/text-diff-expansion.ts (L239-248)
```typescript
  const newLines = newContentLines.slice(
    Math.max(from - 1, 0),
    Math.min(to - 1, newContentLines.length)
  )
  const numberOfLinesToAdd = newLines.length

  // Nothing to do here
  if (numberOfLinesToAdd === 0) {
    return
  }
```
