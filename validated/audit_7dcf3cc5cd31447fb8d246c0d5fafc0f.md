### Title
Command Injection via Unescaped Repository Path in Windows Shell Launch - (File: app/src/lib/shells/win32.ts)

### Summary
The `launch()` function in `app/src/lib/shells/win32.ts` builds shell command lines by directly interpolating the repository's local file-system path into a string that is executed via `spawn(..., { shell: true })`, without escaping shell metacharacters such as `&`, `%`, or `^` (all of which are legal in Windows file/directory names). Because the repository path is derived from a directory name that can be seeded from an attacker-controlled Git remote URL (via `sanitizeCloneName` in `app/src/lib/remote-parsing.ts`, which only strips path separators and traversal sequences, not shell metacharacters), a malicious repository/remote URL can produce a local folder name that breaks out of the quoted command string when the user later invokes "Open in Shell/Command Prompt."

### Finding Description
`launch()` for shells like Git Bash, PowerShell, Hyper, Alacritty, Cygwin, and Warp builds argument strings by naive string interpolation of `path`: [1](#0-0) 

All of these calls pass `{ shell: true }` to `spawn`, which on Windows causes Node to hand the joined command line to `cmd.exe /d /s /c` for shell interpretation, meaning any un-escaped `cmd.exe` metacharacter in `path` (`&`, `%`, `^`, `|`) is interpreted rather than treated as literal text: [2](#0-1) 

The `path` value originates from the repository's on-disk location. When cloning from a generic (non-GitHub) remote URL, the destination folder name is derived from the URL via `sanitizeCloneName`, which only guards against path traversal (`/`, `\`, `:`, `..`) — it does not strip or reject shell metacharacters: [3](#0-2) 

This sanitized (but not shell-escaped) name is joined into the clone destination path in the Clone dialog: [4](#0-3) 

Note that the Windows-specific character filter `safeDirectoryName` (which strips `<>:"|?*`) is applied only in the "Create New Repository" flow (`app/src/ui/lib/repository-path.tsx`), not in the clone flow. Windows disallows `<>:"|?*` in filenames but **does allow** `&`, `%`, and `^` — exactly the characters `cmd.exe` treats specially — so a crafted remote URL/repo name containing these characters survives both the OS filesystem restrictions and `sanitizeCloneName`, landing intact as the on-disk directory name.

### Impact Explanation
If a victim clones a repository from a URL whose last path segment (repo name) contains a `cmd.exe` metacharacter (e.g., `evil & calc.exe & echo`), the cloned folder is created with that literal name on disk. Later, when the victim uses GitHub Desktop's "Open in Shell" feature (a common, expected user action) on that repository, `launch()` in `win32.ts` interpolates the folder name unescaped into a `shell: true` command line, causing `cmd.exe` to execute the injected command with the user's privileges. This satisfies the "attacker controls a cloned/fetched repository ... result is code execution" impact criterion.

### Likelihood Explanation
Exploitation requires: (1) the victim clones an attacker-hosted or attacker-named repository (a normal, expected Desktop workflow for "Clone repository from URL"), and (2) the victim subsequently clicks "Open in [Shell]" for that repository — also a normal, one-click Desktop feature, not an "unnatural" step. No local access, admin rights, or pre-existing malware is required. The main uncertainty is whether GitHub.com repository name validation would block such characters for GitHub-hosted clones — however, the Clone dialog's "URL" tab and non-GitHub Git hosting services do not enforce GitHub's naming restrictions, so the generic-URL clone path remains exploitable.

### Recommendation
- Avoid `shell: true` when constructing these Windows shell-launch commands; pass arguments as an array to `spawn` without shell interpretation wherever the target application supports it, as already done in the macOS implementation (`app/src/lib/shells/darwin.ts`).
- Where `shell: true` is unavoidable (e.g., to use `START`), use the existing `app/src/lib/hooks/shell-escape.ts` `cmdEscape` routine (already implemented for hook environment shells) to properly escape the `path` argument before interpolation.
- Alternatively, reject or sanitize directory names containing `cmd.exe` metacharacters (`&`, `%`, `^`, `|`) at clone time in `sanitizeCloneName`, in addition to the existing path-traversal protections.

### Proof of Concept
1. On Windows, clone a generic Git URL whose repository name ends in a segment such as `repo & calc.exe & rem` (permitted by NTFS, not blocked by `sanitizeCloneName`).
2. GitHub Desktop creates the local directory `...\GitHub\repo & calc.exe & rem`.
3. In Desktop, select that repository and choose "Open in Git Bash" (or Cmd/PowerShell/etc.).
4. `launch()` executes `spawn(gitBashPath, ['--cd="...\\repo & calc.exe & rem"'], { shell: true, cwd: path })`; `cmd.exe` interprets the `&` as command separators and runs `calc.exe` with the victim's privileges. [1](#0-0) [3](#0-2)

### Citations

**File:** app/src/lib/shells/win32.ts (L491-524)
```typescript
  switch (shell) {
    case Shell.PowerShell:
      return spawn('START', ['"PowerShell"', `"${foundShell.path}"`], {
        shell: true,
        cwd: path,
      })
    case Shell.PowerShellCore:
      return spawn(
        'START',
        [
          '"PowerShell Core"',
          `"${foundShell.path}"`,
          '-WorkingDirectory',
          `"${path}"`,
        ],
        {
          shell: true,
          cwd: path,
        }
      )
    case Shell.Hyper:
      const hyperPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${hyperPath}`)
      return spawn(hyperPath, [`"${path}"`], {
        shell: true,
        cwd: path,
      })
    case Shell.Alacritty:
      const alacrittyPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${alacrittyPath}`)
      return spawn(alacrittyPath, [`--working-directory "${path}"`], {
        shell: true,
        cwd: path,
      })
```

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

**File:** app/src/lib/remote-parsing.ts (L88-116)
```typescript
export function sanitizeCloneName(name: string): string | null {
  const components = name.split(/[/\\:]/)

  let lastComponent = ''
  for (let i = components.length - 1; i >= 0; i--) {
    if (components[i].length > 0) {
      lastComponent = components[i]
      break
    }
  }

  if (lastComponent.length === 0) {
    return null
  }

  if (lastComponent.endsWith('.git')) {
    lastComponent = lastComponent.slice(0, -4)
  }

  if (
    lastComponent === '..' ||
    lastComponent === '.' ||
    lastComponent.length === 0
  ) {
    return null
  }

  return lastComponent
}
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L613-626)
```typescript
    const tabState = this.getSelectedTabState()
    const lastParsedIdentifier = tabState.lastParsedIdentifier
    const safeName = lastParsedIdentifier
      ? sanitizeCloneName(lastParsedIdentifier.name)
      : null
    const directory = safeName ? Path.join(path, safeName) : path

    this.setSelectedTabState(
      { path: directory, error: null },
      this.validatePath
    )

    return directory
  }
```
