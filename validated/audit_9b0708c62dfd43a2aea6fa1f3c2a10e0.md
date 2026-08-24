### Title
Windows shell-launch command injection via unescaped repository path with `shell:true` - (File: `app/src/lib/shells/win32.ts`)

### Summary
GitHub Desktop already has a dedicated shell-escaping utility (`app/src/lib/hooks/shell-escape.ts`, notably `cmdEscape`/`cmd.quoteCommand`) that is used when building shell command lines for git-hook environment capture (`app/src/lib/hooks/get-shell-env.ts`). However, the "Open in shell" feature for Windows (`app/src/lib/shells/win32.ts`, `launch()`) builds its command lines with raw string interpolation of the repository path and executes them with Node's `spawn(..., { shell: true })`, instead of routing through the existing escape helpers. This is the same class of defect as the reported Solidity bug: a "properly typed/validated" call path exists elsewhere in the codebase, but a different code path uses a lower-level, unchecked mechanism (raw string built manually + `shell:true`) instead.

### Finding Description
`launch()` in [1](#0-0)  builds Windows shell command lines by directly interpolating the `path` argument into template strings such as:

```ts
case Shell.GitBash:
  const gitBashPath = `"${foundShell.path}"`
  return spawn(gitBashPath, [`--cd="${path}"`], { shell: true, cwd: path })
```

and for Cygwin:
```ts
case Shell.Cygwin:
  return spawn(cygwinPath, [`/bin/sh -lc 'cd "$(cygpath "${path}")"; exec bash`], { shell: true, cwd: path })
```

Because `shell: true` causes Node to hand the whole string to `cmd.exe /d /s /c "..."` for parsing, any `cmd.exe` metacharacter present in `path` (e.g. `&`, `^`, `%`, or a literal `"`) can break out of the intended argument and inject additional shell syntax. The codebase is aware that this class of interpolation is unsafe — it maintains `cmdEscape` in [2](#0-1)  specifically to neutralize `%`, `&`, `<`, `>`, `^`, `|`, and `"` — but `win32.ts`'s `launch()` does not use it.

The `path` value that reaches `launch()` originates from the repository's on-disk directory path, which is partly attacker-influenced when the folder name is derived from a cloned repository's name. `sanitizeCloneName()` in [3](#0-2)  only strips path-separator characters (`/`, `\`, `:`) and a trailing `.git`, and rejects `.`/`..`; it does **not** strip shell metacharacters such as `&`, `%`, or `^`, all of which are valid Windows filename characters. This sanitized name is then joined into the clone destination path in `app/src/ui/clone-repository/clone-repository.tsx` (`onChooseWithOpenDialog`, `updateUrl`) at [4](#0-3) , and this path is what later gets passed to `dispatcher.openShell()` → `win32.ts` `launch()` when the user selects "Open in Git Bash/Command Prompt/etc." for that repository.

### Impact Explanation
If the local clone-destination folder name (derived from a remote/URL-supplied repository name) contains `cmd.exe` metacharacters, a user opening a shell from Desktop for that repository could trigger arbitrary command execution on Windows, because the unescaped path is fed into a `shell:true` spawn. This is a code-execution primitive triggered purely by content the user did not author (a repository name from a URL/clone target), matching the "unprivileged, attacker-controls-a-cloned/fetched-repository-object" impact class.

### Likelihood Explanation
The GitHub.com/GitHub Enterprise clone flows constrain repository names to GitHub's own naming rules (alphanumerics, `-`, `_`, `.`), which would prevent `&`/`%`/`^` from appearing, reducing likelihood for those tabs. However, the "Clone repository" dialog's **URL/Generic tab** and `parseRemote()`'s generic regexes in [5](#0-4)  accept arbitrary non-GitHub git remote URLs, whose final path segment (the repo "name") is not restricted to GitHub's charset, and `sanitizeCloneName()` does not filter shell metacharacters. This weakens the guarantee that a folder name is "shell-safe" and is the direct root cause the fix should address; I was not able to fully trace whether deep-link ("x-github-client://openRepo/...", see [6](#0-5) ) driven clone flows reuse the exact same sanitize/join logic, so the end-to-end reachability from a single untrusted click (as opposed to a manually-entered URL in the Clone dialog) is not fully confirmed with the tools available.

### Recommendation
- In `app/src/lib/shells/win32.ts`, stop building shell command strings via raw template-literal interpolation of `path`/`foundShell.path`. Reuse the existing `cmd.quoteCommand`/`cmdEscape` helpers from `app/src/lib/hooks/shell-escape.ts` (already used for `get-shell-env.ts`) for every branch of `launch()`, or avoid `shell: true` entirely by passing arguments as an array to `spawn()` without shell interpretation.
- Harden `sanitizeCloneName()` in `app/src/lib/remote-parsing.ts` to also reject/strip characters that are unsafe as shell metacharacters on the target platform (at minimum `%`, `&`, `^`, `"`, backtick, `$`), not just path separators and traversal segments.

### Proof of Concept
Conceptual PoC (not fully verified end-to-end due to index limitations on the deep-link/clone-URL join path):
1. Attacker hosts a non-GitHub git remote whose URL's final path segment is `evil&calc.exe&.git` (valid because `parseRemote`'s generic SSH/HTTPS regexes and `sanitizeCloneName` do not reject `&`).
2. Victim clones this URL via Desktop's "Clone repository → URL" tab. `sanitizeCloneName` returns `evil&calc.exe&`, and the folder is created on disk as `.../evil&calc.exe&` (a legal Windows directory name).
3. Victim later selects "Open in Git Bash" (or Command Prompt) for that repository from Desktop's repository menu, invoking `dispatcher.openShell(path)` → `win32.ts` `launch()`.
4. `launch()` builds `spawn(gitBashPath, ['--cd="C:\\...\\evil&calc.exe&"'], { shell: true, cwd: path })`; `cmd.exe` parses the unescaped `&` as a command separator, executing `calc.exe` (or any attacker-chosen command) in place of the intended git-bash invocation.

Because this relies on Windows-specific `shell:true` interpolation and the exact reachability of a fully attacker-supplied folder name through the clone UI, this should be validated hands-on in a Devin session before treating it as a confirmed exploitable path.

### Citations

**File:** app/src/lib/shells/win32.ts (L485-531)
```typescript
export function launch(
  foundShell: FoundShell<Shell>,
  path: string
): ChildProcess {
  const shell = foundShell.shell

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
    case Shell.GitBash:
      const gitBashPath = `"${foundShell.path}"`
      log.info(`launching ${shell} at path: ${gitBashPath}`)
      return spawn(gitBashPath, [`--cd="${path}"`], {
        shell: true,
        cwd: path,
      })
```

**File:** app/src/lib/hooks/shell-escape.ts (L33-46)
```typescript
// https://github.com/ericcornelissen/shescape/blob/89072ba7de233f81f5553b52098671c94eb9bd0c/src/internal/win/cmd.js#L35
const cmdEscape = (arg: string) =>
  arg
    .replace(/[\0\u0008\r\u001B\u009B]/gu, '')
    .replace(/\n/gu, ' ')
    .replace(/"/gu, '""')
    .replace(/([%&<>^|])/gu, '"^$1"')
    .replace(/(?<!\\)(\\*)(?="|$)/gu, '$1$1')

export const cmd: Shell = {
  args: ['/d', '/s', '/c'],
  quoteCommand: (cmd, ...args) =>
    `"${[cmd, ...args].map(a => `"${cmdEscape(a)}"`).join(' ')}"`,
}
```

**File:** app/src/lib/remote-parsing.ts (L27-52)
```typescript
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L659-674)
```typescript
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
```

**File:** app/test/unit/parse-app-url-test.ts (L26-34)
```typescript
  describe('openRepo via HTTPS', () => {
    it('returns right name', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/desktop/desktop'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/desktop/desktop')
```
