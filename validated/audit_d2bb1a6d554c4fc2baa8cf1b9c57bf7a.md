`readFile` here is `readFile` from `fs/promises`, imported directly at the top of `app/src/lib/git/diff.ts`, which follows symlinks by default (no `lstat`/`realpath` containment check is performed anywhere in this function). [1](#0-0) 

### Title
Symlink-following file read in `getWorkingDirectoryImage` discloses arbitrary file contents outside the repository - (File: app/src/lib/git/diff.ts)

### Summary
`getWorkingDirectoryImage` builds a path by joining `repository.path` with the attacker/repo-controlled `file.path` and reads it with `fs/promises`'s `readFile`, which transparently dereferences symlinks. A malicious repository can commit a symlink with an image-like extension (e.g. `evil.png`) pointing to an arbitrary file on the victim's machine. When the working tree is checked out with symlink support enabled (default on macOS/Linux, and possible on Windows when the developer mode / symlink privilege is enabled), selecting that entry in the Changes list triggers this code path and the target file's bytes are read, base64-encoded, and embedded as a data URI `Image` that is rendered in the UI.

### Finding Description
`getWorkingDirectoryImage` computes `Path.join(repository.path, file.path)` and calls `readFile(...)` directly. [2](#0-1)  Unlike a defensive implementation that would `lstat`/`realpath` the resolved path and verify it still resolves inside `repository.path`, this function performs no such containment check — it relies purely on string-joining `repository.path` and `file.path`, and `file.path` is untrusted (comes from a name that can be committed by anyone with write access to a repository, e.g. a malicious remote/fork). [3](#0-2)  Because `fs/promises.readFile` follows symlinks by default, if the checked-out file `file.path` is actually a symlink (as Git can store and check out symlinks as regular tree entries with mode `120000`), the read will resolve through the symlink to whatever target it points to, even if that target is entirely outside `repository.path` (e.g., `/etc/passwd`, SSH keys, or other sensitive files readable by the OS user running Desktop). The bytes read from that external target are then base64-encoded into an `Image` object and rendered as an `<img>` data URI in the diff view, which the user (and, via DOM/dev-tools inspection or "copy image", potentially an attacker who can get the user to interact further) can access. This breaks the invariant that only content genuinely inside `repository.path` should ever be surfaced by the diff viewer.

### Impact Explanation
This allows a malicious repository to induce arbitrary file read of any file the Desktop process's OS user can access, disclosed through the rendered image diff (a File Read Outside the Repo primitive), which matches the "read outside the repo" category explicitly called out as valid impact.

### Likelihood Explanation
Requires that (a) the malicious repo/branch is cloned or fetched and checked out by the victim, (b) the OS/Git configuration allows creating symlinks on checkout (default on macOS/Linux; requires elevated privilege/Developer Mode on Windows), and (c) the victim opens the Changes tab and selects the affected binary-looking file so the image diff is rendered. No other explicit safeguard against symlinked paths was found for this function elsewhere in `diff.ts`, `file-system.ts` context referenced by the question, or the general codebase search for `symlink` protections related to diff/image rendering. [2](#0-1) 

### Recommendation
Before calling `readFile`, resolve the file with `fs.lstat`/`fs.realpath` on the joined path and verify: (1) it is not a symlink (or if symlinks must be supported, resolve them and confirm the final real path still starts with the resolved real path of `repository.path` plus a path separator), and reject/skip rendering otherwise.

### Proof of Concept
1. Create a repository containing a symlink `evil.png -> /etc/passwd` (on a symlink-enabled Git config) and commit it.
2. Have the victim clone/fetch this repository in GitHub Desktop with symlink checkout enabled.
3. In the Changes list, select `evil.png` (detected/added as a binary/image file).
4. Observe that the rendered image diff's data URI, when base64-decoded, matches the contents of `/etc/passwd` (or whatever target the attacker chose) instead of any content that was ever part of the repository blob.

### Citations

**File:** app/src/lib/git/diff.ts (L26-26)
```typescript
import { readFile, writeFile, unlink } from 'fs/promises'
```

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
