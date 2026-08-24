## Analysis

Yes — `readGitIgnoreAtRoot` builds the path with a plain `Path.join` and passes it directly to `FS.readFile`, with no symlink check: [1](#0-0) 

Node's `fs.readFile` follows symlinks by default (unlike `fs.lstat`), so if `<repo>/.gitignore` is a symlink to an out-of-repo path, `readGitIgnoreAtRoot` will transparently return the target file's contents.

This is called directly from `RepositorySettings.componentWillMount`, which puts the result straight into component state and renders it in the `GitIgnore` textarea with no path/containment validation: [2](#0-1) [3](#0-2) [4](#0-3) 

Notably, the codebase already has a dedicated symlink-escape guard, `resolveWithin` (which uses `realpath` to verify the resolved target stays under the intended root), and it is used elsewhere for exactly this class of risk (e.g. `copilot-conflict-context.ts` explicitly comments "Guard against path traversal and symlink escapes"): [5](#0-4) [6](#0-5) 

`gitignore.ts`'s `readGitIgnoreAtRoot` and `saveGitIgnore` do not use `resolveWithin` or any `lstat`/symlink check before reading/writing `.gitignore` at `Path.join(repository.path, '.gitignore')`: [7](#0-6) 

Since Git supports committing symlinks (mode `120000`) and checks them out as real OS symlinks on clone/fetch (on macOS/Linux by default, and on Windows when symlink support is enabled), a malicious repository can ship a `.gitignore` symlink pointing to an absolute path outside the repo (e.g. `/etc/passwd` or `~/.ssh/id_rsa`). When the victim clones this repo and opens **Repository Settings → Ignored Files**, Desktop will read and display the target file's contents in the textarea — an out-of-repo file disclosure. Worse, `saveGitIgnore` uses `writeFile` on the same non-verified path, so if the user clicks "Save" (even without editing, since the dialog auto-saves changed state), the write would follow the symlink and overwrite the external target — a potential arbitrary file **write** as well, though that requires the "Save" action to trigger with `ignoreTextHasChanged` set.

I could not fully verify the current default behavior of `core.symlinks` when cloning via the app's own `clone.ts` wrapper (i.e., whether GitHub Desktop's bundled Git config disables symlink checkout by default), which is relevant to real-world exploitability; this would need to be confirmed with an actual clone test.

### Title
Symlinked `.gitignore` in a cloned repo causes out-of-repo file disclosure (and potential write) via Repository Settings - (File: app/src/lib/git/gitignore.ts)

### Summary
`readGitIgnoreAtRoot` resolves `.gitignore` with `Path.join` and reads it with `FS.readFile`, which follows symlinks. A cloned repository can ship a `.gitignore` symlink pointing outside the repository root, and opening Repository Settings will display the target file's contents.

### Finding Description
`readGitIgnoreAtRoot` (`app/src/lib/git/gitignore.ts:14-32`) computes `ignorePath = Path.join(repository.path, '.gitignore')` and passes it to `FS.readFile`. Node's `fs.readFile` dereferences symlinks. No `lstat`/`resolveWithin`-style containment check is applied, unlike other parts of the codebase (`app/src/lib/path.ts`'s `resolveWithin`, used in `copilot-conflict-context.ts`) that explicitly guard against symlink escapes for repository-relative file reads.

### Impact Explanation
An attacker-controlled repository can commit `.gitignore` as a symlink to a sensitive absolute path. On clone/checkout, Git recreates this as a real filesystem symlink. Opening the victim's Repository Settings → Ignored Files tab causes Desktop to read and render the contents of the external target file in the UI — an out-of-repo information disclosure. If the user subsequently saves the dialog, `saveGitIgnore`'s `writeFile` would follow the same symlink and overwrite the target file, escalating to an out-of-repo write.

### Likelihood Explanation
Requires the victim to clone the malicious repo and open the Ignored Files tab in Repository Settings — a plausible, low-friction interaction that doesn't need unnatural steps, satisfying the "attacker controls a cloned/fetched repository" criterion. Exploitability depends on the platform's symlink checkout behavior (enabled by default on macOS/Linux; conditionally on Windows), which was not independently verified against this fork's clone configuration.

### Recommendation
Before reading or writing `.gitignore`, verify the resolved path does not traverse a symlink outside the repository root — e.g., use `resolveWithin(repository.path, '.gitignore')` (already available in `app/src/lib/path.ts`) or `fs.lstat`/`fs.realpath` checks, and refuse to read/write if the path is a symlink or resolves outside `repository.path`.

### Proof of Concept
1. On a POSIX system, create a repository containing a symlink: `ln -s /etc/passwd .gitignore`, `git add -A`, commit, push.
2. Clone this repository with GitHub Desktop.
3. Open the repository → **Repository → Repository Settings → Ignored Files**.
4. Observe that `readGitIgnoreAtRoot` returns and the textarea renders the contents of `/etc/passwd` instead of a normal `.gitignore`.

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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L99-109)
```typescript
  public async componentWillMount() {
    try {
      const ignoreText = await readGitIgnoreAtRoot(this.props.repository)
      this.setState({ ignoreText })
    } catch (e) {
      log.error(
        `RepositorySettings: unable to read root .gitignore file for ${this.props.repository.path}`,
        e
      )
      this.setState({ errors: [`Could not read root .gitignore: ${e}`] })
    }
```

**File:** app/src/ui/repository-settings/repository-settings.tsx (L234-242)
```typescript
      case RepositorySettingsTab.IgnoredFiles: {
        return (
          <GitIgnore
            text={this.state.ignoreText}
            onIgnoreTextChanged={this.onIgnoreTextChanged}
            onShowExamples={this.onShowGitIgnoreExamples}
          />
        )
      }
```

**File:** app/src/ui/repository-settings/git-ignore.tsx (L27-34)
```typescript
        <TextArea
          ariaLabel="Ignored files"
          ariaDescribedBy="ignored-files-description"
          placeholder="Ignored files"
          value={this.props.text || ''}
          onValueChanged={this.props.onIgnoreTextChanged}
          textareaClassName="gitignore"
        />
```

**File:** app/src/lib/path.ts (L36-71)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-400)
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
```
