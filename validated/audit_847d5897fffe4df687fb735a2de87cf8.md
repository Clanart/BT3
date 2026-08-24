### Title
Symlinked working-directory image file causes `readFile` to follow the link outside the repository — (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryImage` builds the path to read purely by joining `repository.path` with `file.path` and passes it straight to Node's `readFile`, which follows symbolic links by default. `sizing.ts`'s `getAspectFitSize`/`getMaxFitSize` only perform arithmetic on the resulting `ISize` and never validate where the bytes came from, so there is no containment check anywhere in this call chain.

### Finding Description
`getWorkingDirectoryImage` reads the binary contents of a changed file for the image-diff viewer: [1](#0-0) 

`file.path` originates from git's own status/diff output (a `FileChange`), so it is always a repo-relative path with no `..` segments required for the attack — the escape does not need path traversal at all. Instead, an attacker crafts a repository whose tree contains a tree entry with git file mode `120000` (a symlink), e.g. `evil.png -> /etc/passwd` (or, on the victim's platform, any absolute/relative path pointing outside the working tree, such as a path into `~/.ssh` or an app config file containing tokens). When the victim clones/checks out this repository, git materializes the symlink on disk exactly as committed. When GitHub Desktop's changes/diff view processes `evil.png` as a binary/image change, it calls `getWorkingDirectoryImage(repository, file)`, which does `Path.join(repository.path, file.path)` → `<repo>/evil.png`, then calls Node's `fs.readFile` on it. Because `fs.readFile` follows symlinks by default and there is no `fs.lstat`/`fs.realpath` + `resolveWithin`-style containment check anywhere in this function or in `getBlobImage`, the call transparently follows the symlink and reads the *target* file's bytes (e.g. `/etc/passwd` or any file readable by the OS user running Desktop).

The resulting `Buffer` is base64-encoded into an `Image` object and handed to the renderer for display. `sizing.ts`'s `getAspectFitSize` and `getMaxFitSize` then only compute layout dimensions from `ISize` (`width`/`height`) derived from whatever image (or garbage) was decoded — they are pure arithmetic functions and contain no path/containment logic: [2](#0-1) 

I searched for any `isSymbolicLink`/`lstat`/`resolveWithin`-style guard around `getWorkingDirectoryImage` or its call sites and found none — the only match in the whole `app/src` tree for symlink-related checks was unrelated code in `custom-integration.ts`.

### Impact Explanation
This allows an attacker who controls repository content to read arbitrary files on the victim's filesystem (limited to files readable by the OS user running GitHub Desktop) by naming the symlink target with an image-like extension. If the read succeeds and is a valid image, its content is rendered directly in the UI (visual disclosure); if it is not valid image data, the file is still fully read into memory and base64-encoded and sent across the IPC boundary to the renderer, which is itself a file-read-outside-the-repo issue regardless of whether it renders. This matches the "file write or read outside the repo" impact category in scope.

### Likelihood Explanation
Likelihood is high: no user action beyond cloning/opening a maliciously crafted (or fetched) repository and viewing the Changes/History diff for the symlinked file is required — this is a normal, expected workflow in Desktop, not an unnatural user step. Git natively supports symlink tree entries (mode `120000`) and checks them out as real filesystem symlinks by default on macOS/Linux (and, if `core.symlinks` is enabled, on Windows too), so crafting the malicious repo is straightforward.

### Recommendation
Before reading a working-directory file for the diff viewer, `lstat` the resolved path and reject (or specially handle, e.g. display "symlink" placeholder) any entry that is a symbolic link, or resolve the real path with `fs.realpath` and verify it stays within `repository.path` using a `resolveWithin`-style containment check, mirroring the described but currently-absent safeguard, before calling `readFile` in `getWorkingDirectoryImage` (and audit `getBlobImage`/other similar readers for the same gap).

### Proof of Concept
1. Create a repository and add a tree entry with git mode `120000` named `evil.png` whose link target is an absolute path to a sensitive file (e.g., `/etc/passwd` or, cross-platform, a path pointing at another sensitive local file), e.g. via `git update-index --add --cacheinfo 120000 <blob-sha-of-target-path-string> evil.png` then commit.
2. Have the victim clone this repository with GitHub Desktop (or fetch/checkout the branch in an existing repo).
3. Modify `evil.png`'s target (or add it as an uncommitted change) so it appears in the Changes list as a binary/image diff.
4. Open the diff for `evil.png` in Desktop's image-diff view; observe that `getWorkingDirectoryImage` (`app/src/lib/git/diff.ts:930`) reads through the symlink and the target file's bytes are base64-encoded and delivered to the renderer, with `sizing.ts` computing layout on the resulting `ISize` without any containment check having been performed.

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

**File:** app/src/ui/diff/image-diffs/sizing.ts (L10-32)
```typescript
export function getAspectFitSize(
  imageSize: ISize,
  containerSize: ISize
): ISize {
  const heightRatio =
    containerSize.height < imageSize.height
      ? imageSize.height / containerSize.height
      : 1
  const widthRatio =
    containerSize.width < imageSize.width
      ? imageSize.width / containerSize.width
      : 1

  let ratio = Math.max(1, widthRatio)
  if (widthRatio < heightRatio) {
    ratio = Math.max(1, heightRatio)
  }

  return {
    width: imageSize.width / ratio,
    height: imageSize.height / ratio,
  }
}
```
