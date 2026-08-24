### Title
Command Injection via Unsanitized Repository-Derived Path in Windows Shell Launchers — ([File: app/src/lib/shells/win32.ts])

### Summary
GitHub Desktop derives the local clone directory name from the last path segment of an attacker-suppliable git remote URL via `sanitizeCloneName`, which only guards against path traversal and does not strip shell metacharacters. That folder path is later interpolated, unescaped, directly into `cmd.exe`-executed command strings (`spawn(..., { shell: true })`) when the user opens a shell/terminal on the repository, allowing arbitrary command execution.

### Finding Description
`sanitizeCloneName` in `app/src/lib/remote-parsing.ts` extracts the last `/`, `\`, or `:`-delimited component of a URL-derived repository name and rejects only traversal-style results (`..`, `.`, empty), stripping a trailing `.git`: [1](#0-0) 

This function is invoked in the clone UI (`app/src/ui/clone-repository/clone-repository.tsx`) to compute the default local folder name from a user-provided or clicked clone URL, together with `parseRepositoryIdentifier`/`parseRemote`. Windows forbids only `< > : " | ? *` in filenames — characters such as `&`, `%`, `(`, `)`, `!`, `^`, and spaces remain valid and pass through `sanitizeCloneName` untouched.

Later, when the user performs "Open in Command Prompt/PowerShell/Git Bash/Cygwin/Warp" on that repository, `launch()` in `app/src/lib/shells/win32.ts` builds shell commands by directly interpolating the repository's on-disk `path` into a double-quoted argument string and executes it with `shell: true` (i.e. via `cmd.exe`): [2](#0-1) 

`cmd.exe` treats `&`, `|`, `^`, and other metacharacters as command separators/escape characters even when they appear inside a double-quoted string — Windows quoting does not neutralize them the way POSIX shell quoting does. Because the folder name (and thus `path`) is attacker-influenced and only wrapped in `"..."` rather than properly escaped for `cmd.exe`, a folder name containing `&calc.exe&` (or similar) breaks out of the quoted argument and is executed as a separate command.

### Impact Explanation
This allows unprivileged, attacker-controlled input (a crafted git remote URL — clicked as a link, pasted in the Clone dialog, or reached via the `x-github-client://openrepo/...` deep link handled in `app/src/lib/parse-app-url.ts` / `app/src/main-process/main.ts`) to achieve arbitrary command execution on the victim's machine the next time they open a terminal/shell for that repository from GitHub Desktop. This matches the required impact class: attacker controls a cloned/fetched repository or a link the user clicks, and the result is code execution — without any local/physical access, admin rights, or pre-existing malware.

### Likelihood Explanation
Requires the victim to (1) clone or open a repository from a crafted URL whose last path segment contains shell metacharacters valid in Windows filenames, and (2) later invoke "Open in [Shell]" on that repository — a common, expected workflow in Desktop. The `deep link → open-repository-from-url` path in `parse-app-url.ts` shows this can be reached without the user manually typing a URL. Likelihood is moderate: it depends on Windows + a non-default("Open in Shell") but very commonly used feature, and on the victim not noticing the unusual folder name shown in Explorer/repository list.

### Recommendation
- In `app/src/lib/shells/win32.ts`, avoid `shell: true` when spawning shells with a repository-derived path, or properly escape all arguments for `cmd.exe` (e.g., use argument arrays without a shell, or a vetted escaping routine such as those used by `child_process` on Windows without `shell: true`).
- Alternatively/additionally, sanitize `sanitizeCloneName` (or apply a similar filter before persisting a clone path) to strip/replace shell-significant characters (`&`, `|`, `^`, `%`, `!`, `(`, `)`, backtick, etc.), not just path-traversal sequences, mirroring the `safeDirectoryName` Windows sanitization already used in `app/src/ui/lib/repository-path.tsx` (`start="23" end="25"`).
- Add regression tests analogous to `app/test/unit/clone-path-safety-test.ts` that assert clone names containing shell metacharacters cannot be turned into a command-injection payload in the `win32.ts` shell launchers.

### Proof of Concept
1. On Windows, use GitHub Desktop's Clone dialog (or a deep link `x-github-client://openrepo/<url>`) with a URL such as `https://example.com/owner/repo & calc.exe & .git`.
2. `sanitizeCloneName` produces the folder name `repo & calc.exe &` (after `.git` stripping), which is a valid Windows directory name and gets joined with the user's chosen base directory to form the clone destination.
3. After cloning, select "Open in Git Bash" (or Cmd/PowerShell) for this repository.
4. `launch()` builds, e.g., `spawn(gitBashPath, ['--cd="C:\Users\victim\...\repo & calc.exe & "'], { shell: true, cwd: path })`; `cmd.exe` interprets the embedded `&` as command separators, launching `calc.exe` (or any attacker-chosen command) with the privileges of the logged-in user.

Note: I was unable to execute this PoC directly (no sandbox/terminal access in this analysis mode); the finding is based on static code review of `win32.ts` and `remote-parsing.ts`. Verification of exact `cmd.exe` quoting/escaping behavior for this specific payload should be confirmed on a live Windows host before relying on this as a fully proven exploit chain.

### Citations

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

**File:** app/src/lib/shells/win32.ts (L525-542)
```typescript
    case Shell.GitBash:
      const gitBashPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${gitBashPath}`)
      return spawn(gitBashPath, [`--cd="${path}"`], {
        shell: true,
        cwd: path,
      })
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
