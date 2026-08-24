This is a valid finding. `saveGitIgnore` builds `ignorePath` with a plain `Path.join` and passes it directly to `writeFile` with no `lstat`/symlink check, unlike `resolveWithin` in `app/src/lib/path.ts` which is used elsewhere (e.g. `dispatcher.ts`, `app-store.ts`) specifically to defend against symlink-based path escapes but is **not** used here.

### Title
Writing to `.gitignore` follows an attacker-controlled symlink, allowing out-of-tree file overwrite - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`saveGitIgnore` resolves the ignore file path with `Path.join(repository.path, '.gitignore')` and writes to it via Node's `fs/promises.writeFile`, which follows symlinks by default (no `O_NOFOLLOW`/`lstat` check). A malicious repository can ship `.gitignore` as a symlink pointing to a file outside the working tree. When the user performs a normal, expected action — editing the ignored files list in Repository Settings, or clicking "Ignore file" from the Changes list context menu — Desktop overwrites the symlink target instead of the tracked file.

### Finding Description [1](#0-0) 

`saveGitIgnore` computes `ignorePath` from `repository.path` and `'.gitignore'` and calls `writeFile(ignorePath, fileContents)` without ever checking whether `.gitignore` is a symlink or whether its resolved target stays inside the repository. Node's `fs.writeFile`/`fs/promises.writeFile` opens the destination path following any symlinks in the path by default, so if `.gitignore` is a symlink, the write lands on the symlink's target, not on a new regular file inside the repo.

The codebase already has a helper, `resolveWithin` (in `app/src/lib/path.ts`), that resolves a path and rejects it if the real (symlink-resolved) path escapes a given root — it's used to guard other user-triggered filesystem operations such as reveal-in-Finder / conflict resolution paths in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/stores/app-store.ts`. `saveGitIgnore` does not use it. [2](#0-1) 

Reachability: `saveGitIgnore` is called from `appendIgnoreRule`/`appendIgnoreFile`, which are used by the "Ignore file"/"Ignore file (all `.foo` files)" context-menu actions in `app/src/ui/changes/sidebar.tsx` and the Repository Settings → Ignored Files editor (`app/src/ui/repository-settings/repository-settings.tsx`), both wired through `app/src/lib/stores/app-store.ts` and `app/src/ui/dispatcher/dispatcher.ts`. [3](#0-2) 

git itself, when checking out a tree, will happily materialize a tracked symlink blob as an OS-level symlink pointing at any target string stored in the blob (e.g. `/home/user/.bashrc` or `../../../.ssh/authorized_keys`); it performs no validation that the target stays inside the repo. So a malicious repository can trivially ship such a `.gitignore` symlink.

### Impact Explanation
If the symlink target is writable by the user (e.g. `~/.bashrc`, `~/.profile`, a Git hook file, an SSH config, etc.), triggering the ignore-file save path silently overwrites that file's contents with gitignore-pattern text, which is a file-write/corruption primitive outside the repository boundary — squarely in the "file write ... outside the repo" and "silent corruption" impact categories. Depending on the target chosen by the attacker (shell rc files, SSH `authorized_keys`, git hooks under `.git/hooks` if those aren't already symlink-protected, etc.), this could escalate from data corruption to code execution on next shell/SSH session, though such escalation is target- and environment-dependent and not directly demonstrated in this codebase.

### Likelihood Explanation
The trigger requires the user to perform an ordinary UI action (use "Ignore file" from the Changes context menu, or open/save the Repository Settings → Ignored Files panel) after cloning an attacker-controlled repository — both are common, expected user workflows, not unnatural steps. The only precondition is that the target OS/filesystem supports symlinks and git checked them out as such (default on macOS/Linux with `core.symlinks=true`, which is the default when the OS supports it). This makes the likelihood reasonably high for macOS/Linux users; Windows behavior depends on whether symlink creation privilege is available during checkout.

### Recommendation
In `saveGitIgnore` (and `readGitIgnoreAtRoot`), validate the `.gitignore` path before writing/reading:
- Use `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` from `app/src/lib/path.ts` to resolve `.gitignore` against `repository.path` and reject (or refuse to write, prompting the user) if the resolved real path is not the direct `.gitignore` file within the repo root.
- Alternatively, `lstat` the target path first; if it's a symlink, refuse to write through it (delete-and-recreate as a regular file, or error out) rather than following it.

### Proof of Concept
1. Create a "malicious" repository containing a `.gitignore` symlink instead of a regular file: `ln -s /tmp/target .gitignore`, commit and push it (symlinks are tracked as blobs by git).
2. Victim clones the repository with GitHub Desktop; `.gitignore` is checked out as a real OS symlink pointing at `/tmp/target`.
3. Victim right-clicks a changed file in the Changes list and selects "Ignore file" (or opens Repository Settings → Ignored Files and edits/saves the list).
4. This invokes `appendIgnoreFile`/`saveGitIgnore` → `writeFile(ignorePath, fileContents)`, which follows the symlink.
5. Inspect `/tmp/target` — its content is now overwritten with the gitignore text, confirming the out-of-tree write. [4](#0-3)

### Citations

**File:** app/src/lib/git/gitignore.ts (L1-60)
```typescript
import * as Path from 'path'
import * as FS from 'fs'
import { Repository } from '../../models/repository'
import { getConfigValue } from './config'
import { writeFile } from 'fs/promises'

/**
 * Read the contents of the repository .gitignore.
 *
 * Returns a promise which will either be rejected or resolved
 * with the contents of the file. If there's no .gitignore file
 * in the repository root the promise will resolve with null.
 */
export async function readGitIgnoreAtRoot(
  repository: Repository
): Promise<string | null> {
  const ignorePath = Path.join(repository.path, '.gitignore')

  return new Promise<string | null>((resolve, reject) => {
    FS.readFile(ignorePath, 'utf8', (err, data) => {
      if (err) {
        if (err.code === 'ENOENT') {
          resolve(null)
        } else {
          reject(err)
        }
      } else {
        resolve(data)
      }
    })
  })
}

/**
 * Persist the given content to the repository root .gitignore.
 *
 * If the repository root doesn't contain a .gitignore file one
 * will be created, otherwise the current file will be overwritten.
 */
export async function saveGitIgnore(
  repository: Repository,
  text: string
): Promise<void> {
  const ignorePath = Path.join(repository.path, '.gitignore')

  if (text === '') {
    return new Promise<void>((resolve, reject) => {
      FS.unlink(ignorePath, err => {
        if (err) {
          reject(err)
        } else {
          resolve()
        }
      })
    })
  }

  const fileContents = await formatGitIgnoreContents(text, repository)
  await writeFile(ignorePath, fileContents)
}
```

**File:** app/src/lib/git/gitignore.ts (L62-100)
```typescript
/** Add the given pattern or patterns to the root gitignore file */
export async function appendIgnoreRule(
  repository: Repository,
  patterns: string | string[]
): Promise<void> {
  const text = (await readGitIgnoreAtRoot(repository)) || ''

  const currentContents = await formatGitIgnoreContents(text, repository)

  const newPatternText =
    patterns instanceof Array ? patterns.join('\n') : patterns
  const newText = await formatGitIgnoreContents(
    `${currentContents}${newPatternText}`,
    repository
  )

  await saveGitIgnore(repository, newText)
}

/**
 * Convenience method to add the given file path(s) to the repository's gitignore.
 *
 * The file path will be escaped before adding.
 */
export async function appendIgnoreFile(
  repository: Repository,
  filePath: string | string[]
): Promise<void> {
  if (filePath instanceof Array) {
    const escapedFilePaths = filePath.map(path =>
      escapeGitSpecialCharacters(path)
    )

    return appendIgnoreRule(repository, escapedFilePaths)
  }

  const escapedFilePath = escapeGitSpecialCharacters(filePath)
  return appendIgnoreRule(repository, escapedFilePath)
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
