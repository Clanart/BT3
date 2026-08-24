### Title
OS Command Injection in "Open in Shell" (Cygwin/Git Bash) via Unescaped Path Interpolation into `shell: true` Spawn - ([File: app/src/lib/shells/win32.ts])

### Summary
On Windows, GitHub Desktop's shell-launch code builds POSIX shell command strings by naively interpolating the working-directory `path` into a nested, double-quoted `$(...)` command substitution and then executes that string with `spawn(..., { shell: true })`. Because the interpolation performs no shell-metacharacter escaping, a `path` value containing shell metacharacters (`$()`, `` ` ``, `"`) — all of which are legal in NTFS/Windows folder names — is executed as a command by the POSIX shell (`sh`/`bash`), not merely used as an argument.

### Finding Description
`launch()` in [1](#0-0)  builds the Cygwin launch command as:
```js
return spawn(
  cygwinPath,
  [`/bin/sh -lc 'cd "$(cygpath "${path}")"; exec bash`],
  { shell: true, cwd: path }
)
```
`${path}` is concatenated directly inside a double-quoted argument to `cygpath`, which is itself inside a `$(...)` command substitution, which is itself inside the single-quoted script passed to `/bin/sh -lc`. In POSIX shells, command substitution (`$(...)`) is still expanded even when nested inside double quotes, so if `path` contains `$(` ... `)` or backticks, that content is executed as a shell command rather than treated as literal text. The neighboring `Shell.GitBash` case similarly builds an unescaped `--cd="${path}"` argument and also spawns with `shell: true` [2](#0-1) .

This is invoked from the `_openShell` app-store action and the `openShell` dispatcher, whose `path` argument ultimately comes from `repository.path` [3](#0-2) [4](#0-3) .

The relevant attacker-influenced value enters through the worktree-creation flow: when a user chooses "Checkout in New Worktree" on a branch, the initial worktree name is pre-populated directly from the (potentially remote/fork-supplied) branch name:
```js
initialWorktreeName: `${this.props.repository.name}-${branch.nameWithoutRemote}`,
``` [5](#0-4) 
Git ref names permit characters such as `$`, `(`, `)`, and backticks. This name is joined into the worktree's filesystem path via `Path.join(path, safeDirectoryName(name))` in `RepositoryPath` [6](#0-5) , and that full path is passed straight to `addWorktree()` [7](#0-6) [8](#0-7) , becoming the new worktree's `repository.path`. If `safeDirectoryName` only strips filesystem-illegal characters (not shell metacharacters, none of which are illegal on Windows), the resulting directory name can retain `$(...)`/backtick sequences. Later, when the user opens that worktree "In Shell" with Cygwin or Git Bash configured, the payload embedded in the directory name is executed by the shell as shown above.

### Impact Explanation
If reached, this results in arbitrary command execution on the victim's machine with the privileges of the logged-in user, triggered by an entirely normal Desktop action ("Checkout in New Worktree" followed by "Open in Shell"), without the user typing or reviewing any command — the payload lives silently in a directory name derived from attacker-controlled branch/ref data.

### Likelihood Explanation
Exploitability depends on: (1) the user having Cygwin or Git Bash configured as their selected/available shell on Windows, (2) the user accepting or not fully sanitizing the pre-filled worktree name when creating a worktree from an attacker-supplied branch, and (3) `safeDirectoryName()` not stripping the specific shell metacharacters (`$`, `(`, `)`, backtick) used by the payload — a detail I could not verify from the indexed code (its implementation was not found in the retrieved context). Because of this unverified sanitization step, likelihood is assessed as **uncertain/medium** rather than confirmed; the core unescaped-interpolation flaw in `win32.ts`, however, is directly verifiable in the code shown above and represents a real broken invariant (shell metacharacters in a filesystem path must never reach a `shell:true` spawn unescaped).

### Recommendation
- Never build POSIX/cmd shell command strings via raw string interpolation of filesystem paths. Use the existing quoting helpers already present in the codebase (`app/src/lib/hooks/shell-escape.ts`) to escape `path` before embedding it in the Cygwin/GitBash/PowerShell/Alacritty/Warp command strings in `win32.ts`, or avoid `shell: true` entirely by passing the target directory as a discrete `spawn` argument/`cwd` instead of interpolating it into a shell script string.
- Audit `safeDirectoryName()` (in `app/src/ui/lib/repository-path.tsx`) to confirm it strips or rejects shell metacharacters (`$`, `` ` ``, `(`, `)`, `;`, `&`, `|`) in addition to filesystem-illegal characters, since these characters are legal on Windows/NTFS but dangerous once such paths reach any `shell:true` invocation elsewhere in the app.

### Proof of Concept
1. Attacker creates/pushes a branch named e.g. `pwn-$(calc.exe)-branch` on a remote the victim will fetch/add (fork, PR, or arbitrary remote).
2. Victim fetches this branch in Desktop and chooses "Checkout in New Worktree…" from the branch dropdown; the worktree name field is pre-filled as `reponame-pwn-$(calc.exe)-branch` [5](#0-4) .
3. Victim accepts (or only partially edits) the suggested name; Desktop creates a worktree directory containing the literal `$(calc.exe)` substring.
4. Victim selects that worktree/repository and invokes "Open in Shell" (Ctrl+`) with Cygwin or Git Bash selected as their shell.
5. `launch()` in `app/src/lib/shells/win32.ts` builds `/bin/sh -lc 'cd "$(cygpath "<path with $(calc.exe)>")"; exec bash'` and spawns it with `shell: true`; the nested `$(calc.exe)` is evaluated by the POSIX shell, executing `calc.exe` (or any attacker-chosen command) on the victim's machine.

### Citations

**File:** app/src/lib/shells/win32.ts (L525-531)
```typescript
    case Shell.GitBash:
      const gitBashPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${gitBashPath}`)
      return spawn(gitBashPath, [`--cd="${path}"`], {
        shell: true,
        cwd: path,
      })
```

**File:** app/src/lib/shells/win32.ts (L532-542)
```typescript
    case Shell.Cygwin:
      const cygwinPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${cygwinPath}`)
      return spawn(
        cygwinPath,
        [`/bin/sh -lc 'cd "$(cygpath "${path}")"; exec bash`],
        {
          shell: true,
          cwd: path,
        }
      )
```

**File:** app/src/ui/app.tsx (L3391-3397)
```typescript
  private openInShell = (repository: Repository | CloningRepository) => {
    if (!(repository instanceof Repository)) {
      return
    }

    this.props.dispatcher.openShell(repository.path)
  }
```

**File:** app/src/lib/stores/app-store.ts (L7576-7592)
```typescript
  public async _openShell(path: string) {
    this.statsStore.increment('openShellCount')
    const { useCustomShell, customShell } = this.getState()

    try {
      if (useCustomShell && customShell) {
        await launchCustomShell(customShell, path, error =>
          this._pushError(error)
        )
      } else {
        const match = await findShellOrDefault(this.selectedShell)
        await launchShell(match, path, error => this._pushError(error))
      }
    } catch (error) {
      this.emitError(error)
    }
  }
```

**File:** app/src/ui/toolbar/branch-dropdown.tsx (L414-422)
```typescript
  private onCheckoutInNewWorktree = (branch: Branch) => {
    this.props.dispatcher.closeFoldout(FoldoutType.Branch)
    this.props.dispatcher.showPopup({
      type: PopupType.AddWorktree,
      repository: this.props.repository,
      initialBranchName: branch.name,
      initialWorktreeName: `${this.props.repository.name}-${branch.nameWithoutRemote}`,
    })
  }
```

**File:** app/src/ui/lib/repository-path.tsx (L123-129)
```typescript
  private getFullPath(): string | null {
    const { name, path } = this.state
    if (path === null || path.length === 0 || name.trim().length === 0) {
      return null
    }
    return Path.join(path, safeDirectoryName(name))
  }
```

**File:** app/src/ui/worktrees/add-worktree-dialog.tsx (L93-110)
```typescript
    try {
      if (branch?.type === BranchType.Remote) {
        // Remote branch: create a new local branch from the remote ref
        await addWorktree(this.props.repository, fullPath, {
          createBranch: branch.nameWithoutRemote,
          commitish: branch.ref,
        })
      } else if (branch) {
        // Existing local branch: check it out in the new worktree
        await addWorktree(this.props.repository, fullPath, {
          commitish: branch.name,
        })
      } else {
        // New branch: create it in the new worktree
        await addWorktree(this.props.repository, fullPath, {
          createBranch: effectiveBranchName,
        })
      }
```

**File:** app/src/lib/git/worktree.ts (L120-143)
```typescript
export async function addWorktree(
  repository: Repository,
  path: string,
  options: {
    /** Branch name used with -b (create new branch) */
    readonly createBranch?: string
    /** Commit-ish to check out (branch name, ref, or SHA) */
    readonly commitish?: string
  } = {}
): Promise<void> {
  const args = ['worktree', 'add']

  if (options.createBranch) {
    args.push('-b', options.createBranch)
  }

  args.push(path)

  if (options.commitish) {
    args.push(options.commitish)
  }

  await git(args, repository.path, 'addWorktree')
}
```
