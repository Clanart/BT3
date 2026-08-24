### Title
Command Injection via Unsanitized Clone Directory Name in Windows Shell Launch - ([File: app/src/lib/shells/win32.ts])

### Summary
`sanitizeCloneName()` in `app/src/lib/remote-parsing.ts` only strips path-traversal characters (`/`, `\`, `:`) from a repository name derived from a clone URL; it does not strip shell metacharacters such as `&`, `|`, `^`, `<`, `>` that are legal in NTFS filenames but are command separators for `cmd.exe`. The resulting folder name becomes the local repository path. When the user later uses GitHub Desktop's "Open in <Shell>" feature on Windows for a shell such as Alacritty, Hyper, Cygwin, or Warp, `launch()` in `app/src/lib/shells/win32.ts` builds the child-process command by embedding this untrusted path inside a double-quoted string and invokes `spawn` with `shell: true`. Because `cmd.exe` treats `&`/`|`/`^` as command separators even inside double quotes, a crafted clone-URL basename lets an attacker append and execute an arbitrary second command.

### Finding Description
`sanitizeCloneName` is the single guard applied to the last path component of a (possibly attacker-supplied) clone URL to derive the on-disk folder name: [1](#0-0) 

It removes `/`, `\`, `:` and traversal segments (`.`, `..`), but characters such as `&`, `|`, `^`, `<`, `>`, `%`, `(`, `)` pass through unchanged — all of these are valid in Windows (NTFS) file/directory names. This function is used directly to build the destination directory when the user is guided through the Clone dialog: [2](#0-1) [3](#0-2) 

Separately, the filesystem-invalid-character sanitizer used elsewhere (`safeDirectoryName` in `app/src/ui/lib/repository-path.tsx`, and `sanitizedRepositoryName` in `app/src/ui/add-repository/sanitized-repository-name.ts`) strips a wider set (`<>:"|?*` or non-word characters), but that logic is not shared with `sanitizeCloneName`, so the clone flow's directory-naming path lacks equivalent shell-safety hardening: [4](#0-3) 

Once a repository exists on disk with such a name, opening it in certain shells constructs a Windows command line where the repository `path` is interpolated directly into a single quoted argument, then executed via `spawn(..., { shell: true })`: [5](#0-4) 

Node's `shell: true` on Windows runs `cmd.exe /d /s /c <joined-args>` without re-quoting the individual array elements for cmd.exe semantics. `cmd.exe` parses `&`, `|`, `^`, `<`, `>` as metacharacters *regardless of surrounding double quotes* (unlike POSIX shells, where quotes fully neutralize them). So a path like `...\repo&calc.exe&` breaks out of the `"${path}"` string and is executed as a second command.

### Impact Explanation
An attacker who controls a git remote/clone URL (e.g., a non-GitHub git host, or a crafted URL delivered through the `github clone <url>` CLI helper or an app deep link that reaches the clone flow) can cause GitHub Desktop to create a local directory whose name contains `cmd.exe` metacharacters. When the victim later performs the ordinary action of opening that repository's shell integration (Alacritty/Hyper/Cygwin/Warp) from within Desktop, arbitrary OS commands execute with the privileges of the Desktop process — full local code execution, matching the "external attacker controls a git remote + code execution" impact class from the original pm2 report.

### Likelihood Explanation
Exploitation requires: (1) the attacker to control the source of a clone URL whose last path segment contains shell metacharacters (not restricted, unlike GitHub.com repo names, when cloning from arbitrary/non-GitHub remotes), and (2) the victim to be on Windows and to invoke "Open in <shell>" for one of the affected shells (Alacritty, Hyper, Cygwin, Warp) on that specific repository. This is a normal, expected user action (not social engineering or unnatural steps), but it is gated on the victim choosing one of a subset of non-default shells, and the primary default (`Cmd`, via `START`) is not built this way. This lowers likelihood relative to a universally-triggered path but the primitive itself requires no special privileges or pre-existing compromise.

### Recommendation
- Extend `sanitizeCloneName` (or apply `sanitizedRepositoryName`/`safeDirectoryName`-style filtering) to strip/replace shell metacharacters (`&`, `|`, `^`, `<`, `>`, `%`, `!`, quotes, parentheses) in addition to path separators before using the value to build the local clone directory.
- In `app/src/lib/shells/win32.ts`, avoid `shell: true` with manually interpolated, quoted paths; instead pass the path as a discrete argv element without a shell, or properly escape it using the existing `cmdEscape`/`shQuoteCommand` helpers already present in `app/src/lib/hooks/shell-escape.ts`.

### Proof of Concept
1. On a non-GitHub git server (or any URL source not subject to GitHub.com's stricter repo-name validation), host a repository whose URL basename is `repo&calc.exe&.git` (or similarly crafted with `&whoami>C:\pwned.txt&`).
2. Have the victim clone this URL in GitHub Desktop; `sanitizeCloneName` returns `repo&calc.exe&` unchanged (aside from the `.git` suffix strip), and this becomes the local folder name, e.g. `C:\Users\victim\Documents\GitHub\repo&calc.exe&`.
3. Victim right-clicks the repository in Desktop and selects "Open in Alacritty" (or Hyper/Cygwin/Warp).
4. `launch()` executes `spawn(alacrittyPath, ['--working-directory "C:\\Users\\victim\\Documents\\GitHub\\repo&calc.exe&"'], { shell: true, cwd: path })`; `cmd.exe` parses the embedded `&` as a command separator and executes `calc.exe` (or the attacker's chosen command) outside of Alacritty's intended argument.

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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L604-626)
```typescript
  private onChooseWithOpenDialog = async (): Promise<string | undefined> => {
    const path = await showOpenDialog({
      properties: ['createDirectory', 'openDirectory'],
    })

    if (path === null) {
      return
    }

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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L648-684)
```typescript
  private updateUrl = async (url: string) => {
    const parsed = parseRepositoryIdentifier(url)
    const tabState = this.getSelectedTabState()
    const lastParsedIdentifier = tabState.lastParsedIdentifier

    // If there is no path yet, just update the url
    if (tabState.path === null) {
      this.setSelectedTabState({ url }, this.validatePath)
      return
    }

    const safeName = parsed ? sanitizeCloneName(parsed.name) : null

    let newPath: string

    const dirPath = tabState.path
    if (lastParsedIdentifier) {
      if (safeName) {
        newPath = Path.join(Path.dirname(dirPath), safeName)
      } else {
        newPath = Path.dirname(dirPath)
      }
    } else if (safeName) {
      newPath = Path.join(dirPath, safeName)
    } else {
      newPath = dirPath
    }

    this.setSelectedTabState(
      {
        url,
        lastParsedIdentifier: parsed,
        path: newPath,
      },
      this.validatePath
    )
  }
```

**File:** app/src/ui/lib/repository-path.tsx (L23-25)
```typescript
const safeDirectoryName = (name: string) => {
  return __WIN32__ ? name.replace(/[<>:"|?*]/g, '-').replace(/\s+$/, '') : name
}
```

**File:** app/src/lib/shells/win32.ts (L511-549)
```typescript
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
    case Shell.Warp:
      const warpPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${warpPath}`)
      return spawn(warpPath, [`warp://action/new_tab?path="${path}"`], {
        shell: true,
        cwd: path,
      })
```
