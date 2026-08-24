### Title
Symlinked `.gitignore` in a cloned repo causes `saveGitIgnore` to write attacker-chosen file content outside the repository root - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`saveGitIgnore` builds the target path with `Path.join(repository.path, '.gitignore')` [1](#0-0)  and then writes to it with `writeFile(ignorePath, fileContents)` [2](#0-1) . Neither `Path.join` nor Node's `fs/promises.writeFile` resolve or check for symlinks — `writeFile` opens the path with the default `'w'` flag, which follows a symlink to its target rather than truncating/replacing the symlink itself.

### Finding Description
If a cloned/fetched repository contains a tracked symlink at `.gitignore` (git supports storing symlinks as blobs with mode `120000`) pointing to a path outside the repository (e.g. `../../.bashrc`, `../../../.ssh/authorized_keys`, or another absolute/relative path reachable on the same filesystem), then after checkout the working-tree entry `.gitignore` is an OS-level symlink to that external target. When the user invokes the "Ignore file" / "Add to .gitignore" action (`appendIgnoreFile` → `appendIgnoreRule` → `saveGitIgnore`) [3](#0-2) , `saveGitIgnore` resolves `ignorePath` as the plain working-tree path to `.gitignore` and calls `writeFile(ignorePath, fileContents)`. Since `.gitignore` is a symlink, Node's `writeFile` dereferences it and writes the formatted gitignore content into the external target file, not into the repository. The same unguarded pattern also exists in `readGitIgnoreAtRoot` (read side) [4](#0-3)  and in the "Apply .gitignore template" flow in `writeGitIgnore` [5](#0-4) , all of which never call `lstat`/`realpath` to confirm the resolved path stays within `repository.path`.

### Impact Explanation
This breaks the repo-root write containment invariant: attacker-controlled repository content can cause GitHub Desktop to overwrite a file outside the repository with content largely composed of the escaped file path the user chose to ignore. The write payload is constrained to gitignore-pattern text (escaped via `escapeGitSpecialCharacters`) rather than arbitrary bytes, so this is a targeted, format-limited overwrite rather than full arbitrary content injection — but it still allows overwriting attacker-chosen sensitive files (dotfiles, config files, shell profiles) elsewhere on disk, which can lead to data corruption or, depending on the target file (e.g. shell rc files, `authorized_keys`), further compromise once the file is later read/executed by other software.

### Likelihood Explanation
Requires the victim to open a malicious repository in GitHub Desktop and explicitly trigger the "Ignore file" (or template-apply) action from the UI (`sidebar.tsx`, `repository-settings.tsx`)  . It does not require any additional confirmation beyond the normal context-menu click, and cloning a repo with a symlinked `.gitignore` is a standard git operation, so the precondition (cloning/checking out attacker content) is well within the stated threat model. Actual write success also depends on OS symlink-following semantics for `.gitignore` (works on POSIX; on Windows, git symlink support and Node's write-follow behavior need repo-specific `core.symlinks` settings), so likelihood is moderate rather than universal.

### Recommendation
Before reading/writing `.gitignore`, verify the path is not a symlink (or that if it is, it resolves within `repository.path`) — e.g., `fs.lstatSync(ignorePath)` and reject/handle symlink entries, or use `fs.realpath` and confirm the resolved path is still inside `repository.path` before calling `writeFile`/`unlink`/`readFile`. Apply the same containment check in `readGitIgnoreAtRoot` and `writeGitIgnore` (`app/src/ui/add-repository/gitignores.ts`).

### Proof of Concept
1. Create a repository containing a tracked symlink `.gitignore -> ../outside-target.txt` (e.g. `ln -s ../outside-target.txt .gitignore && git add .gitignore && git commit`), where `outside-target.txt` lives just outside the repo directory.
2. Clone this repository with GitHub Desktop.
3. In the Changes view, right-click an untracked file and choose "Ignore file" (this calls `appendIgnoreFile` → `saveGitIgnore`) [6](#0-5) .
4. Observe that `outside-target.txt`, located outside the repository root, is overwritten with the gitignore pattern content rather than a new/updated file being confined to the repository.

### Citations

**File:** app/src/lib/git/gitignore.ts (L14-32)
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
```

**File:** app/src/lib/git/gitignore.ts (L40-44)
```typescript
export async function saveGitIgnore(
  repository: Repository,
  text: string
): Promise<void> {
  const ignorePath = Path.join(repository.path, '.gitignore')
```

**File:** app/src/lib/git/gitignore.ts (L58-59)
```typescript
  const fileContents = await formatGitIgnoreContents(text, repository)
  await writeFile(ignorePath, fileContents)
```

**File:** app/src/lib/git/gitignore.ts (L62-79)
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
```

**File:** app/src/lib/git/gitignore.ts (L86-99)
```typescript
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
```

**File:** app/src/ui/add-repository/gitignores.ts (L49-57)
```typescript
/** Write the named gitignore to the repository. */
export async function writeGitIgnore(
  repositoryPath: string,
  name: string
): Promise<void> {
  const fullPath = Path.join(repositoryPath, '.gitignore')
  const text = await getGitIgnoreText(name)
  await writeFile(fullPath, text)
}
```
