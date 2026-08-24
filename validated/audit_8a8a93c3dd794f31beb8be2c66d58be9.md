No protective checks (no `lstat`, no symlink/mode filtering, no `TypeChanged` exclusion) were found guarding `readPartialFile` or `getNewFileContent` in the syntax-highlighting path, confirming the gap.

### Title
Symlink working-tree files bypass content-source invariant in syntax highlighting, allowing arbitrary file read from disk - (File: app/src/ui/diff/syntax-highlighting/index.ts)

### Summary
`getNewFileContent` in `app/src/ui/diff/syntax-highlighting/index.ts` reads the "new contents" of a working-directory `ChangedFile` directly from disk via `readPartialFile(Path.join(repository.path, file.path), 0, MaxHighlightContentLength - 1)` [1](#0-0) . `readPartialFile` opens the path with Node's `createReadStream(path, { start, end })` [2](#0-1) , which follows symlinks by default and has no `lstat`/symlink check anywhere in this call chain.

### Finding Description
If an attacker-controlled repository contains, at `file.path`, a symlink (git mode `120000`) whose target resolves to a path elsewhere on the user's filesystem (including, but not limited to, files inside `.git/`, such as hook scripts, or files entirely outside the repository, e.g. `/etc/passwd`, SSH keys, or other sensitive user files), checking out that repository creates an OS-level symlink at that working-tree path. When the user then views that file's diff in the "Changes" pane, `getNewFileContent` joins `repository.path` with `file.path` and calls `readPartialFile`, which transparently dereferences the symlink and streams the *target file's* bytes rather than the symlink's own content (the target path string that git actually tracks) [3](#0-2) [2](#0-1) . No component in this path performs an `lstat` or otherwise validates that `file.path` is a regular file before reading it — I confirmed via search that there is no `lstat`/`S_IFLNK`/`isSymbolicLink` check gating `readPartialFile` or `getNewFileContent`.

This differs from `getBlobContents`/`getPartialBlobContents` in `app/src/lib/git/show.ts`, which resolve content through `git show <commitish>:<path>`, entirely inside git's object model — git never dereferences on-disk symlinks for this operation, it just returns the blob (the recorded symlink target string) [4](#0-3) . The vulnerability is specifically in the working-directory branch of `getNewFileContent`, which reads from the live filesystem instead of git's object store.

### Impact Explanation
The resulting buffer — containing bytes from an attacker-chosen filesystem path, not the tracked blob — is decoded and rendered as "new contents" for syntax highlighting and diff display [5](#0-4) . This is a silent, unintended disclosure of file content outside the tracked repository state into the Desktop UI, satisfying the "read outside the repo" impact category. Depending on the symlink target, this can expose secrets or files the user did not intend to share, and misrepresents to the user what is actually tracked/committed for that path (a "corrupted" view of file content, though it does not directly corrupt what is *committed*, since committing/staging still uses git's own symlink-aware object model).

### Likelihood Explanation
Exploitation only requires the victim to clone/checkout an attacker-authored repository containing a symlink blob and then open that file in the Changes/diff view — a normal workflow, requiring no unusual user action beyond browsing a hostile repo's changes, which is within the defined threat model (attacker controls cloned/fetched repository content).

### Recommendation
Before calling `readPartialFile` in `getNewFileContent`, check the working-tree entry's mode/type (e.g., via `fs.lstat`/`fs.promises.lstat` on the resolved path, or by using the status entry's git mode already surfaced by `git status --porcelain=2`, which reports the on-disk mode) and refuse to read/highlight the file — or explicitly treat it as a symlink/type-change diff — if it is a symbolic link rather than a regular file.

### Proof of Concept
1. Create a repository containing a symlink blob: `ln -s .git/hooks/post-checkout secret-link && git add secret-link && git commit -m "add symlink"`.
2. Modify the symlink target file content (e.g., write attacker text into `.git/hooks/post-checkout`) or point the symlink outside the repo at a sensitive file, then re-checkout so the working tree has the symlink but git status shows it as changed/new.
3. In Desktop, select the file in Changes and open its diff; `getFileContents` → `getNewFileContent` → `readPartialFile(Path.join(repository.path, 'secret-link'), 0, MaxHighlightContentLength-1)` returns the target file's bytes rather than a symlink-target string, and this content is shown/highlighted in the UI. [1](#0-0)

### Citations

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L81-94)
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
