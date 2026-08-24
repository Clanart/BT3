No protections found (no `protectNTFS`/`protectHFS` config, no `lstat`/realpath containment checks) anywhere in the code paths reachable from `appendIgnoreRule`.

### Title
Symlink-following file write escape via attacker-controlled `.gitignore` in `appendIgnoreRule` / `saveGitIgnore` — (File: `app/src/lib/git/gitignore.ts`)

### Summary
`appendIgnoreRule` always resolves the gitignore file with a fixed, non-attacker-influenced path — `Path.join(repository.path, '.gitignore')` — so the `patterns` argument itself cannot cause `..`-style traversal. [1](#0-0) [2](#0-1) 
However, the file-system operations used to read and write that path — `FS.readFile` in `readGitIgnoreAtRoot` and `writeFile`/`FS.unlink` in `saveGitIgnore` — are standard Node fs calls that transparently follow symbolic links. [3](#0-2) [4](#0-3) 
If a cloned/checked-out repository contains a tracked symlink at `.gitignore` (a valid git blob with symlink mode) pointing outside the working tree (e.g. to `../../../.ssh/authorized_keys` or an absolute path), then any Desktop flow that calls `appendIgnoreFile`/`appendIgnoreRule` (e.g. the "Ignore file" context-menu action wired up in `app/src/ui/changes/sidebar.tsx` and `app/src/ui/dispatcher/dispatcher.ts`) will read/write through that symlink and overwrite the attacker-chosen target file with attacker-influenced content instead of the intended repository-local `.gitignore`.

### Finding Description
The name/path of the file being written is always fixed (`.gitignore` under `repository.path`), so there is no `..`-traversal of the *path string* itself — this specific worry from the question doesn't apply directly. The actual escape vector is different: it's not the path that's malicious, it's the *file object* at that fixed path. Git tracks symlinks as first-class objects, and when Desktop checks out an attacker's repository with `core.symlinks` enabled (the default on macOS/Linux, and on Windows when the user/host allows symlink creation), a tracked path named `.gitignore` can be materialized on disk as a real OS-level symlink pointing anywhere, including outside the repository. `readGitIgnoreAtRoot`/`saveGitIgnore` never check with `lstat` whether `.gitignore` is a symlink before calling `FS.readFile`/`writeFile`/`FS.unlink`; these calls dereference symlinks by default. No code in `appendIgnoreRule`'s call chain performs a containment check (e.g., comparing `fs.realpathSync` output against the repository root) before touching the file.

### Impact Explanation
An attacker who gets a victim to clone/fetch/checkout their repository and later perform an "Ignore file" action (or any flow reaching `appendIgnoreRule`/`saveGitIgnore`) can cause Desktop to overwrite or delete an arbitrary file outside the repository that the OS user has write access to, via the attacker-planted `.gitignore` symlink. This matches the stated Critical impact class ("Content of a repository the user merely clones ... symlinks ... target scope: arbitrary file write/replace outside the working tree").

### Likelihood Explanation
Requires the victim to check out the malicious repo with symlink support enabled (default on macOS/Linux; conditional on Windows) and to trigger a gitignore-appending action (a common, easily reachable UI flow — right-click a file → "Ignore file"). No unusual user steps beyond normal repository interaction are needed once the symlink is checked out.

### Recommendation
Before reading or writing the gitignore path, perform an `lstat` on `.gitignore` (and any parent path components) and refuse to proceed (or delete-and-recreate the entry as a regular file) if it is a symlink, or otherwise verify with `fs.realpath` that the resolved location remains strictly inside `repository.path`. The same containment check should be applied generally anywhere Desktop writes to well-known repository-relative paths (`.gitignore`, `.git/config`, etc.) that could be replaced by a tracked symlink from an untrusted checkout.

### Proof of Concept
Conceptual (would need to be validated by a background Devin session, since this repo's test harness runs actual `git` via `dugite`):
1. Create a repository containing a symlink entry named `.gitignore` pointing to a file outside the repo (e.g. `ln -s ../victim.txt .gitignore` then `git add .gitignore` — git stores it as a symlink blob).
2. Clone/checkout this repository with Desktop (or simulate via `setupFixtureRepository`) on a platform where `core.symlinks` is honored.
3. Call `appendIgnoreRule(repo, ['node_modules'])` or trigger "Ignore file" from the UI.
4. Assert that the file at the symlink target (`../victim.txt`, outside the repo root) was created/modified with the ignore-rule content instead of (or in addition to) `repo/.gitignore`.

Note: I could not execute this PoC or definitively confirm platform-specific symlink checkout behavior (Windows `core.symlinks` gating) within the available tools/index; a background Devin session with filesystem/terminal access would be needed to reproduce and confirm end-to-end.

### Citations

**File:** app/src/lib/git/gitignore.ts (L17-17)
```typescript
  const ignorePath = Path.join(repository.path, '.gitignore')
```

**File:** app/src/lib/git/gitignore.ts (L19-31)
```typescript
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
```

**File:** app/src/lib/git/gitignore.ts (L40-60)
```typescript
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
