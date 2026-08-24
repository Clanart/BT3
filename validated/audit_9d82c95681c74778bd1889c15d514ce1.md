### Title
Windows shell-launch command injection via unsanitized repository path containing shell metacharacters - (File: `app/src/lib/shells/win32.ts`)

### Summary
GitHub Desktop's Windows "Open in Shell" feature builds shell command strings by directly interpolating the repository's local folder path into a double-quoted argument, then executes it with `shell: true` (which runs through `cmd.exe`). The folder name that ends up as `path` can be derived from an attacker-controlled GitHub repository name (via clone URL, deep link, or "Clone with Desktop") that is only sanitized against path-traversal characters, not shell metacharacters. This mirrors the `treekill` bug class: Windows-only string concatenation into a shell-executed command with unescaped external input.

### Finding Description
`launch()` in [1](#0-0)  builds command strings such as:
```
case Shell.GitBash:
  const gitBashPath = `"${foundShell.path}"`
  return spawn(gitBashPath, [`--cd="${path}"`], { shell: true, cwd: path })
case Shell.Cygwin:
  ...
  return spawn(cygwinPath, [`/bin/sh -lc 'cd "$(cygpath "${path}")"; exec bash`], { shell: true, cwd: path })
```
`path` here is `repository.path`, passed unescaped into a double-quoted (or single-quoted, for Cygwin) argument and executed with `shell: true`, which on Windows spawns `cmd.exe /d /s /c <string>`. If `path` contains a `"` (or, for the Cygwin case, a `` ` `` or `$()`), the quoting can be broken out of and additional commands injected — the exact same "concatenation without sanitization on Windows" pattern flagged in the `treekill` report (`kill('3333332 & echo ... & ')` breaking out of a Windows command string).

The value of `path` traces back to `repository.path`, which is the local clone directory name. That name can be derived from a GitHub repository's name (an API/URL-controlled value) via `sanitizeCloneName()` in [2](#0-1) . This function only strips path separators (`/`, `\`, `:`), rejects `..`/`.`/empty, and strips a trailing `.git` — it does **not** filter other Windows-significant characters such as `"`, `` ` ``, `&`, `|`, `^`, `%`, or `$`. Separately, `safeDirectoryName()` in [3](#0-2)  does strip `<>:"|?*` — but only for manually-typed repository names in the Create/Clone dialog UI text field, not for the `sanitizeCloneName` path used when the repository name is derived directly from a URL/API object.

Existing guards (`isClonePathSensitive` in [4](#0-3)  and `sanitizeCloneName`) defend against path-traversal into sensitive directories, but do not address shell-metacharacter injection into a folder name that is later concatenated into a shell command string when the user opens that repository in a shell.

### Impact Explanation
If an attacker can get a victim to clone or open a repository whose name (as reported by a GitHub API response, a crafted clone URL, or a `x-github-client://`/`github-desktop://` deep link) contains a double quote or other cmd.exe metacharacter, the resulting local folder name inherits that character. When the victim later uses Desktop's "Open in Shell" (Git Bash or Cygwin) on that repository — a completely ordinary, expected user action — the injected characters can break out of the quoted argument passed to `cmd.exe /d /s /c`, resulting in arbitrary command execution on the victim's Windows machine. This satisfies the "attacker controls a cloned/fetched repository ... and result is code execution" impact criterion.

### Likelihood Explanation
Requires: (1) attacker can present a malicious repository name/URL with shell-metacharacters that survives `sanitizeCloneName`, (2) victim clones/adds it in Desktop on Windows, and (3) victim clicks "Open in Shell" with Git Bash or Cygwin selected. No local access, admin rights, or leaked credentials are needed — only normal Desktop usage on a crafted repository. This is a plausible but not fully proven path since I could not fully verify from the index whether GitHub's own repo-name validation would already reject a name containing `"` before it reaches `sanitizeCloneName` (GitHub.com repository names are typically restricted to `[\w.-]`, which would block quote characters) — this is the main uncertainty affecting real-world exploitability for `clone-github-repository` flows. It remains more directly reachable via `clone-generic-repository` (arbitrary/generic Git URLs) or deep links that don't necessarily pass through GitHub's server-side name validation.

### Recommendation
- Sanitize the clone/repository folder name against Windows-reserved and shell-metacharacter sets (`<>:"|?*&^%$\`'`) in `sanitizeCloneName`, not just path separators — mirroring what `safeDirectoryName` already does for manual entry.
- In `app/src/lib/shells/win32.ts`, avoid manual string interpolation of `path` into shell command strings; use the existing `shell-escape.ts` `quoteCommand` helpers (already used for `printenvz`/hook shell invocation in `get-shell-env.ts`/`get-shell.ts`) consistently for `launch()` as well, or pass arguments as an array without `shell: true` where the target program supports it.

### Proof of Concept
1. Attacker hosts/serves a generic Git remote or deep link (`x-github-client://openRepo/https://evil.example/owner/repo"^&calc.exe^&".git`) whose repository name, once run through `sanitizeCloneName`, still contains a `"` character (e.g., name component like `repo"&calc.exe&"`).
2. Victim clones or adds this repository in GitHub Desktop on Windows; the resulting local folder name embeds the injected characters.
3. Victim opens the repository in Desktop and selects "Open in Shell" with Git Bash (or Cygwin) configured as the shell.
4. `launch()` constructs `--cd="<folder-name>"` and calls `spawn(gitBashPath, [...], { shell: true, cwd: path })`; the embedded `"` breaks out of the quoted string inside the `cmd.exe /d /s /c` invocation, allowing the attacker-controlled suffix to execute as a separate command.

### Citations

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

**File:** app/src/lib/remote-parsing.ts (L72-116)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
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

**File:** app/src/ui/lib/repository-path.tsx (L23-25)
```typescript
const safeDirectoryName = (name: string) => {
  return __WIN32__ ? name.replace(/[<>:"|?*]/g, '-').replace(/\s+$/, '') : name
}
```

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```
