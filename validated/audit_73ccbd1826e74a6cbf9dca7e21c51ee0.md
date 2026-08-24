### Title
Cygwin shell launch command injection via unescaped single quote in repository path - (File: `app/src/lib/shells/win32.ts`)

### Summary
`launch()`'s `Shell.Cygwin` branch builds a Cygwin/`sh -lc` command line by directly string-interpolating the repository `path` into a POSIX shell command that is itself wrapped in single quotes, without escaping single quotes contained in `path`. Since Windows path/folder names may legally contain the `'` character (it is not one of the reserved characters `< > : " / \ | ? *`), an attacker who controls the on-disk folder name of a repository (e.g., via the repository/clone name suggested from a GitHub API object, or a maliciously named folder a user is induced to clone into) can break out of the single-quoted `sh -lc '...'` argument and inject arbitrary shell commands that execute when the user opens "Open in Cygwin" for that repository.

### Finding Description
The vulnerable code: [1](#0-0) 

```ts
case Shell.Cygwin:
  const cygwinPath = `"${foundShell.path}"`
  log.info(`launching ${shell} at path: ${cygwinPath}`)
  return spawn(
    cygwinPath,
    [`/bin/sh -lc 'cd "$(cygpath "${path}")"; exec bash'`],
    {
      shell: true,
      cwd: path,
    }
  )
```

The `path` parameter to `launch()` is the local repository directory path, which flows in from `launch(foundShell, path)` called via the "Open in <shell>" feature (`app/src/ui/dispatcher/dispatcher.ts` / `error-handlers.ts`). This value is neither escaped nor validated for shell-special characters before being embedded inside the string that is ultimately parsed by the Cygwin `sh -lc` invocation.

The intended structure is:
```
'cd "$(cygpath "<path>")"; exec bash'
```
The outer single quotes are meant to prevent the shell from interpreting anything inside as separate commands. However, since `path` is inserted verbatim, a single quote character embedded anywhere in the directory name terminates the outer single-quoted string early. Everything following that point (until the next quote/terminator) is interpreted as unquoted shell syntax by `sh`, allowing command separators (`;`, `&&`, backticks, `$()`) to execute.

For example, if the repository's local folder is named:
```
repo'; touch pwned; echo '
```
the resulting string passed to `sh -lc` becomes:
```
cd "$(cygpath "...repo'; touch pwned; echo '")"; exec bash
```
The first `'` closes the outer quoting early, `; touch pwned; echo '` is then parsed as a live, unquoted shell command sequence — `touch pwned` executes.

Windows filesystems reject `"`, `\`, `/`, `:`, `*`, `?`, `<`, `>`, `|` in file/folder names, but they permit `'`, `` ` ``, `$`, `(`, `)`, and `;`. GitHub Desktop generally derives the default clone directory name from the repository name (which can be attacker-controlled if the attacker owns/names the repository being cloned, or supplies a crafted repo/organization name that a victim clones), so this is a realistic attacker-controlled string that can reach this sink.

### Impact Explanation
If exploited, this results in arbitrary command execution in the context of the logged-in Windows user the moment they choose "Open in Cygwin" for a repository whose local path contains the crafted single-quote payload. This satisfies the "code execution via attacker-controlled repository content" impact criterion in the program's valid-impact list.

### Likelihood Explanation
Exploitation requires: (1) the victim to have Cygwin installed and configured as an available shell in GitHub Desktop, and (2) the victim to actively invoke "Open in Cygwin" on a repository whose folder path contains the malicious single-quote sequence, which in turn requires the victim to have cloned/opened a repository into a folder with that specific name (e.g., accepting a default clone path derived from an attacker-named repo, or manually creating/renaming a folder to match a malicious repo). This is a real but non-default and non-automatic user action, making likelihood moderate rather than high — it needs the specific combination of Cygwin being configured and the "Open in Cygwin" action being triggered.

### Recommendation
Do not interpolate `path` directly into the shell command string. Instead:
- Pass `path` as a properly escaped/quoted POSIX shell argument (e.g., replace `'` with `'\''` within the single-quoted segment), or
- Avoid constructing a single shell string entirely; instead pass the target directory via an environment variable or a separate `-lc` script argument that does not require string concatenation with untrusted data, or
- Validate/reject repository paths containing shell metacharacters before allowing "Open in shell" actions, consistent with how other shell launch branches in the same file (e.g., `Shell.GitBash`, `Shell.Hyper`) should also be reviewed for similar quoting issues.

### Proof of Concept
1. Clone or create a local repository directory named (or containing a path component) such as:
   `C:\Users\victim\repos\demo'; touch %TEMP%\\pwned; '`
   (Windows permits `'` in folder names.)
2. In GitHub Desktop, with Cygwin configured as an installed/available shell, open that repository and select "Open in Cygwin".
3. `launch()`'s `Shell.Cygwin` branch spawns:
   `"<mintty.exe>" /bin/sh -lc 'cd "$(cygpath "C:\Users\victim\repos\demo'; touch %TEMP%\pwned; '")"; exec bash'`
4. When Cygwin's `sh` parses `-lc`'s argument, the embedded `'` closes the outer quoting early and `touch %TEMP%\pwned` (or any attacker-chosen command) executes as an independent shell command. [2](#0-1) [1](#0-0)

### Citations

**File:** app/src/lib/shells/win32.ts (L485-489)
```typescript
export function launch(
  foundShell: FoundShell<Shell>,
  path: string
): ChildProcess {
  const shell = foundShell.shell
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
