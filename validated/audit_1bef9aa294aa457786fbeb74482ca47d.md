### Title
`.gitignore` symlink-following write allows repo-controlled arbitrary file write outside repository - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`readGitIgnoreAtRoot` and `saveGitIgnore` compute `ignorePath` via a plain `Path.join(repository.path, '.gitignore')` and then read/write that path with `fs.readFile`/`fs.promises.writeFile`, both of which follow symlinks by default. Unlike `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` in [1](#0-0) , which are used elsewhere in the codebase (`dispatcher.ts`, `app-store.ts`, `copilot-conflict-context.ts`) to `realpath`-verify a path stays inside the repo root, `gitignore.ts` never validates that `.gitignore` is a regular file confined to the repository.

### Finding Description [2](#0-1) [3](#0-2) 

Both `ignorePath` computations join `repository.path` with the literal `.gitignore` and hand it straight to Node's fs APIs with no `lstat` check and no `O_NOFOLLOW`/`realpath` validation. If the on-disk `.gitignore` entry is a symlink pointing outside the repository, `readGitIgnoreAtRoot` reads through it and `saveGitIgnore`'s `writeFile(ignorePath, fileContents)` (default flag `'w'`) truncates and writes through it to the symlink target.

Because git can track and check out symbolic links as part of ordinary repository content (this is default behavior on macOS/Linux, and is reachable on Windows too), an attacker-controlled repository can simply commit a tracked symlink named `.gitignore` pointing at an absolute path outside the repo (e.g. a dotfile, shell rc file, or SSH config in the user's home directory). No timing race, background process, or hook execution is actually required for the write to land outside the repo — the moment the user performs any UI action that calls `appendIgnoreFile`/`appendIgnoreRule` (e.g. the "Ignore file (add to .gitignore)" context-menu action wired through `sidebar.tsx` → `dispatcher.appendIgnoreFile` → `app-store.ts` `_appendIgnoreFile` → `appendIgnoreRule`/`saveGitIgnore`), the write follows the symlink deterministically. [4](#0-3) [5](#0-4) 

Regarding the specific TOCTOU scenario posed in the question (an intervening git hook triggered by a fetch swapping the file mid-operation): this is not the primary reachable mechanism. Git hooks live in `.git/hooks`, which is not populated from remote/tracked repository content by a normal clone/fetch (hooks are local-only unless the user has separately configured `core.hooksPath` to point at a repo-tracked directory), so a "background process spawned from repo content" cannot reliably be triggered by an intervening fetch in the default configuration. The genuinely reachable and simpler bug is the direct, non-racy symlink-follow described above, which requires no TOCTOU window at all — the vulnerable behavior exists at write time regardless of race timing.

### Impact Explanation
An attacker who gets a victim to clone or open a malicious repository can make GitHub Desktop write attacker-influenced content to an arbitrary file outside the repository the moment the user clicks "Ignore file" (or any other gitignore-editing action, including the Repository Settings gitignore editor which also calls `saveGitIgnore`). This is a file-write-outside-repo primitive driven purely by repository content, matching the "file write... outside the repo" impact category in scope.

### Likelihood Explanation
Requires the victim to interact with the Changes list context menu ("Ignore file"/"Ignore folder") or edit the gitignore settings dialog after opening a malicious repository — a normal, low-friction UI action a user might perform without suspecting danger. Symlink checkout support depends on platform/`core.symlinks` settings, which is typically enabled by default on macOS/Linux.

### Recommendation
Before reading or writing `ignorePath` in `readGitIgnoreAtRoot`/`saveGitIgnore`, validate the path with `fs.lstat` (reject if the existing entry is a symlink) or use `resolveWithin`/`realpath`-based validation (as already used elsewhere in the codebase, e.g. `app/src/lib/path.ts`) to ensure the resolved path stays inside `repository.path` before performing the read/write/unlink.

### Proof of Concept
1. Create a repository containing a tracked symlink named `.gitignore` pointing to an absolute path outside the repo, e.g. `ln -s /home/victim/.bashrc .gitignore && git add .gitignore && git commit -m x`.
2. Have the victim clone this repository with GitHub Desktop.
3. In the Changes view, right-click any untracked file and choose "Ignore file (add to .gitignore)".
4. Observe that `~/.bashrc` (or whatever the symlink target is) is overwritten/appended with gitignore-formatted content instead of the file inside the repo. [6](#0-5)

### Citations

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

**File:** app/src/lib/git/gitignore.ts (L14-60)
```typescript
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

**File:** app/src/ui/changes/sidebar.tsx (L269-271)
```typescript
  private onIgnoreFile = (file: string | string[]) => {
    this.props.dispatcher.appendIgnoreFile(this.props.repository, file)
  }
```

**File:** app/src/lib/stores/app-store.ts (L7929-7943)
```typescript
  public async _appendIgnoreRule(
    repository: Repository,
    pattern: string | string[]
  ): Promise<void> {
    await appendIgnoreRule(repository, pattern)
    return this._refreshRepository(repository)
  }

  public async _appendIgnoreFile(
    repository: Repository,
    filePath: string | string[]
  ): Promise<void> {
    await appendIgnoreFile(repository, filePath)
    return this._refreshRepository(repository)
  }
```
