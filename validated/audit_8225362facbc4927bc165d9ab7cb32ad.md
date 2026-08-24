## Title
Arbitrary File Read via Symlink in Working-Directory Image Diff Rendering — (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryImage()` builds the path to read purely by joining `repository.path` with the attacker-controlled `file.path` from a git status entry, then calls Node's `readFile()` on it with no symlink check, no `realpath`/containment validation, and no `lstat` inspection.

### Finding Description
`getWorkingDirectoryImage(repository, file)` computes the file location as: [1](#0-0) 

`Path.join(repository.path, file.path)` performs a purely lexical join and does not resolve or validate the resulting path. Node's `fs/promises.readFile` (imported at [2](#0-1)  ) follows symlinks transparently by default. If `file.path` refers to a working-tree entry that is a symbolic link (a valid git blob mode `120000`) pointing to an absolute path outside the repository (e.g. `/etc/hosts` or any other file readable by the OS user running Desktop), `readFile` will happily open and return the **target** file's contents rather than the symlink itself.

The caller only needs to reach this function for a file whose status is not `AppFileStatusKind.Deleted` (per the reported classification) — i.e. a modified/new symlink status entry. Nothing in this function (or, as far as could be located in the code searched, in its callers in `app/src/ui/diff/index.tsx`, `seamless-diff-switcher.tsx`, etc.) performs an `lstat` check to detect a symlink and refuse to treat it as an image, nor does it verify that the resolved real path stays within `repository.path`.

### Impact Explanation
This allows a malicious repository to cause Desktop to read and base64-encode an arbitrary file from the victim's filesystem (any file readable by the OS user, e.g. `/etc/hosts`, `~/.ssh/id_rsa`, credential files, SSO tokens, etc.) purely by including a specially crafted symlink entry and having the user open the "Changes" diff view for that path. The result is then wrapped as an `Image` object and rendered as a data URI in the Desktop UI (`<img>` tag), meaning the file's contents become visible to the user — and if the file is later re-committed or otherwise surfaced, could also be exfiltrated. This matches the "file read outside the repo" impact category.

### Likelihood Explanation
Exploitation requires only that the victim clones/opens an attacker-authored repository containing a symlink (e.g., named to look like an image, `link.png -> /etc/hosts` or `~/.aws/credentials`), stages/modifies it, and opens its diff in the Changes view — all normal, expected user interactions with an untrusted repository, not "unnatural steps." No admin rights or local access are required from the attacker's side.

### Recommendation
Before calling `readFile` in `getWorkingDirectoryImage`, use `fs.lstat` (not `fs.stat`) on the joined path and reject/short-circuit if `isSymbolicLink()` is true (or resolve via `fs.realpath` and verify the result is still contained within `repository.path`). This mirrors the containment check that should exist anywhere `repository.path` is joined with a git-tracked path derived from repository content.

### Proof of Concept
1. Create a repository containing a symlink: `ln -s /etc/hosts link.png`, commit it.
2. Modify the symlink target metadata (or restage) so git status reports it with a non-`Deleted` `AppFileStatusKind`.
3. Open GitHub Desktop, select the repository, open the Changes view, and click on `link.png` to view its diff.
4. Desktop calls `getWorkingDirectoryImage`, which resolves through the symlink and reads `/etc/hosts`, rendering its contents as an "image" (or failing to decode but still having read the file's bytes into memory/UI). [1](#0-0) 

**Note on confidence**: I was unable to fully trace, within the available searches, the exact status-classification code path that guarantees a deleted-then-recreated symlink is classified as non-`Deleted` (the reporter's specific proof-idea detail), nor could I confirm with certainty that no upstream caller (e.g. in `app/src/ui/diff/index.tsx` or `seamless-diff-switcher.tsx`) performs a symlink/lstat check before invoking `getWorkingDirectoryImage`. Based on the code reviewed, no such check exists in `diff.ts` itself, and no symlink-detection or path-containment logic was found in the surrounding image-diff code searched.

### Citations

**File:** app/src/lib/git/diff.ts (L26-26)
```typescript
import { readFile, writeFile, unlink } from 'fs/promises'
```

**File:** app/src/lib/git/diff.ts (L926-936)
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
```
