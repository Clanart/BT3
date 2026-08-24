## Title
Symlink Following in `getWorkingDirectoryImage` Allows Arbitrary Host File Disclosure via Image Diff - (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryImage` reads working-directory "image" files with a plain `Path.join(repository.path, file.path)` + `readFile`, without any symlink-safety check. If a repository replaces a tracked image file with a symlink (git mode `120000`) pointing to an absolute path outside the repository, checking out that repository creates a real filesystem symlink at `file.path`. When the user opens the image diff for that path, Node's `readFile` transparently follows the symlink and returns the target file's bytes, which are then base64-encoded and rendered as the `current`/`previous` `Image` consumed by `DifferenceBlend`/`ModifiedImageDiff`.

### Finding Description
`getWorkingDirectoryImage` in `app/src/lib/git/diff.ts` is: [1](#0-0) 

This is called from `getImageDiff` whenever a `WorkingDirectoryFileChange` is not deleted: [2](#0-1) 

Node's `fs.readFile`/`fs/promises.readFile` follows symbolic links by default (no `lstat`/`O_NOFOLLOW`). The path passed in is simply `repository.path` joined with the git-relative `file.path`, with no containment check and no verification that the target is a regular file rather than a symlink.

This codebase already has a purpose-built defense for exactly this class of bug: `resolveWithin` in `app/src/lib/path.ts` resolves a path via `realpath` and verifies the resolved real path is still under the repository root, explicitly rejecting symlink escapes: [3](#0-2) 

That helper is used to guard file reads in the Copilot merge-conflict flow: [4](#0-3) 

But `getWorkingDirectoryImage` (and similarly `getResolutionDiff` and `getNewFileContent` for syntax highlighting) do not use `resolveWithin` or any equivalent symlink check — they read straight through `Path.join` + `readFile`: [5](#0-4) [6](#0-5) 

The rendered `Image` (bytes + base64) is passed unmodified through `IImageDiff` → `Diff.renderImage` → `ModifiedImageDiff` → `DifferenceBlend`, which puts it into an `<img>` element via `ImageContainer`: [7](#0-6) [8](#0-7) 

### Impact Explanation
An attacker who controls a repository's content (a cloned/fetched repo) can commit a symlink (mode `120000`) at a path that git tracks as an "image" file (e.g. `logo.png`) pointing to an arbitrary absolute path on the victim's host (e.g. an SSH private key, browser cookie DB, or any file readable by the Desktop process). When the victim clones/checks out this branch and opens the changes/diff view for that file, GitHub Desktop reads the symlink target's raw bytes and renders them as a base64 data URL in the DOM (`current`/`previous.contents`), from which they are exfiltratable via devtools (or, depending on content, could partially render as an actual broken/valid image, or leak length/size via the "Size" field in `TwoUp`). This is an out-of-repository file read (arbitrary file disclosure) primitive driven purely by attacker-controlled repository content — matching the "file read outside the repo" impact category.

### Likelihood Explanation
Requires only that the victim clones a malicious/compromised repository (or checks out a malicious branch/PR) and opens the diff for the affected path — a very ordinary GitHub Desktop workflow with no unusual user action required beyond viewing a diff. On macOS/Linux, `core.symlinks` is enabled by default so `git checkout` materializes real filesystem symlinks; on Windows, this requires the repo/host to have symlink support enabled (Developer Mode or admin privileges for `core.symlinks=true`), so the practical likelihood is materially higher on macOS/Linux than default Windows configurations.

### Recommendation
Harden `getWorkingDirectoryImage` (and the other working-directory readers noted above, `getResolutionDiff`'s `baseContent` read and `getNewFileContent`) to refuse to follow symlinks when resolving `file.path`:
- Reuse `resolveWithin`/`realpath`-based containment checks already implemented in `app/src/lib/path.ts`, or
- `lstat` the target path first and reject (or explicitly handle as a special "symlink" diff state) if `stats.isSymbolicLink()` is true, before calling `readFile`.

### Proof of Concept
1. Create a repository containing a tracked image `logo.png`.
2. In a new commit, replace `logo.png`'s tree entry with a symlink blob (mode `120000`) whose content is an absolute path to a sensitive file, e.g.:
   ```
   git rm --cached logo.png
   ln -s /Users/victim/.ssh/id_rsa logo.png
   git add logo.png
   git commit -m "update logo"
   ```
3. Have the victim clone/fetch and check out this commit in GitHub Desktop (on macOS/Linux with default symlink support).
4. In the Changes/History view, select `logo.png` and open the image diff.
5. Observe that `getWorkingDirectoryImage` → `readFile(Path.join(repository.path, 'logo.png'))` follows the symlink and returns the contents of `/Users/victim/.ssh/id_rsa`, base64-encoded and embedded as the `current`/`previous` `Image`; inspect via devtools (React props / DOM `<img src="data:...">`) to extract the private key bytes.

### Citations

**File:** app/src/lib/git/diff.ts (L460-463)
```typescript
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```

**File:** app/src/lib/git/diff.ts (L628-631)
```typescript
    // Does it even exist in the working directory?
    if (file.status.kind !== AppFileStatusKind.Deleted) {
      current = await getWorkingDirectoryImage(repository, file)
    }
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

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L89-94)
```typescript
  if (file instanceof WorkingDirectoryFileChange) {
    return readPartialFile(
      Path.join(repository.path, file.path),
      0,
      MaxHighlightContentLength - 1
    )
```

**File:** app/src/ui/diff/index.tsx (L148-158)
```typescript
  private renderImage(imageDiff: IImageDiff) {
    if (imageDiff.current && imageDiff.previous) {
      return (
        <ModifiedImageDiff
          onChangeDiffType={this.props.onChangeImageDiffType}
          diffType={this.props.imageDiffType}
          current={imageDiff.current}
          previous={imageDiff.previous}
        />
      )
    }
```

**File:** app/src/ui/diff/image-diffs/difference-blend.tsx (L20-41)
```typescript
    return (
      <div className="image-diff-difference" ref={this.props.onContainerRef}>
        <div className="sizing-container">
          <div className="image-container" style={style}>
            <div className="image-diff-previous">
              <ImageContainer
                image={this.props.previous}
                onElementLoad={this.props.onPreviousImageLoad}
                style={maxSize}
              />
            </div>

            <div className="image-diff-current">
              <ImageContainer
                image={this.props.current}
                onElementLoad={this.props.onCurrentImageLoad}
                style={{
                  ...maxSize,
                  mixBlendMode: 'difference',
                }}
              />
            </div>
```
