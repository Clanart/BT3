Found a strong analog: the "unneeded risky primitive" pattern maps to Desktop's `showFolderContents` → `UNSAFE_openDirectory` path, which uses `shell.openPath()` on a directory path derived from repository content, where `shell.openPath()` (like `call.value()`) is a documented "dangerous primitive that executes what it opens" versus a safer alternative (`shell.showItemInFolder`, which only reveals a path in Explorer/Finder without executing it).

### Title
Unsafe use of `shell.openPath()` on repository-derived paths can execute attacker-planted executables - ([File: app/src/main-process/shell.ts])

### Summary
`UNSAFE_openDirectory` calls Electron's `shell.openPath()`, which — unlike `shell.showItemInFolder()` — actually *executes/opens* the target with the OS default handler. Its own doc comment warns "This method should never be used to open user-provided or derived paths" and calls out the exact Windows collision risk (`C:\MyFolder\foo` vs `C:\MyFolder\foo.exe`). Yet `showFolderContents()` in `app/src/ui/main-process-proxy.ts` calls it directly on `!__DARWIN__` platforms with a caller-supplied path, with no verification besides `stats.isDirectory()`, and the callable path can originate from repository-derived data (e.g. the repository root itself, or paths built from working-directory entries such as submodules).

### Finding Description
`app/src/main-process/shell.ts` defines: [1](#0-0) 
which explicitly documents that it must never be called with "user-provided or derived paths" because Windows will resolve `foo` to `foo.exe` if both exist in the same directory, causing the executable to launch instead of the folder opening.

`app/src/ui/main-process-proxy.ts`'s `showFolderContents()` calls `UNSAFE_openDirectory(path)` unconditionally on Windows/Linux once `stats.isDirectory()` is true: [2](#0-1) 

On macOS there is an additional app-bundle check (`isApplicationBundle`) before allowing `UNSAFE_openDirectory`, but on Windows/Linux there is **no equivalent guard against the folder/executable name-collision problem the function's own comment warns about**. This is invoked from `_showRepository` / `shell.showFolderContents(repository.path)`: [3](#0-2) 

The broken invariant mirrors the report's `call.value()` vs `send()` pattern: a strictly more dangerous primitive (`shell.openPath`, which executes) is used where a strictly safer primitive (`shell.showItemInFolder`, which only reveals/selects without executing) would accomplish the same UX goal, and the code's own comments acknowledge this is unsafe for derived paths.

### Impact Explanation
If an attacker can cause a directory entry named like `<repo-root-name>.exe` (or a same-named executable at the level Windows would resolve) to exist alongside the folder Desktop is asked to open — e.g., via a cloned/fetched repository containing files that get materialized on disk, or a submodule directory layout — invoking "Show in Explorer" / opening a submodule folder could cause Windows to execute that binary instead of opening the folder, i.e., code execution outside the sandbox, driven by attacker-controlled repository content.

### Likelihood Explanation
This requires a fairly specific naming collision on disk (folder name vs. sibling `.exe` with identical base name) and only affects Windows/Linux (Linux impact is unclear since the collision mechanic described is Windows-specific `ShellExecute` behavior — I could not confirm whether an equivalent collision exists on Linux via `xdg-open`). I was not able to fully trace every caller of `showFolderContents`/`revealInFileManager` to confirm a code path where the *exact* colliding filename is fully attacker-controlled end-to-end (e.g., whether repository root folder names or submodule directory names can be attacker-chosen and cloned onto disk with a colliding sibling file) — this would need further verification in a live/Devin session with filesystem access.

### Recommendation
Replace the Windows/Linux branch's use of `UNSAFE_openDirectory`/`shell.openPath` in `showFolderContents()` with `shell.showItemInFolder()` (the safe primitive already used for the non-directory and macOS-app-bundle cases), consistent with the existing code comment's own guidance, unless there's a concrete UX reason `openPath` is required — analogous to the report's recommendation to prefer `send()` over `call.value()` absent a specific need for the riskier call.

### Proof of Concept
Not independently verified/reproduced — this is inferred directly from the code's own documented warning in `app/src/main-process/shell.ts` (lines 10-13) combined with the unconditional call site in `app/src/ui/main-process-proxy.ts` (lines 116-121). A concrete PoC would require: (1) confirming a reachable path where a repository-controlled directory name can collide with a sibling `.exe` on disk, and (2) triggering `showFolderContents`/`revealInFileManager` on that directory — this should be validated in a full Devin session with disk/OS access, which I do not have here.

### Citations

**File:** app/src/main-process/shell.ts (L1-32)
```typescript
import { shell } from 'electron'

/**
 * Wraps the inbuilt shell.openItem path to address a focus issue that affects macOS.
 *
 * When opening a folder in Finder, the window will appear behind the application
 * window, which may confuse users. As a workaround, we will fallback to using
 * shell.openExternal for macOS until it can be fixed upstream.
 *
 * CAUTION: This method should never be used to open user-provided or derived
 * paths. It's sole use is to open _directories_ that we know to be safe, no
 * verification is performed to ensure that the provided path isn't actually
 * an executable.
 *
 * @param path directory to open
 */
export function UNSAFE_openDirectory(path: string) {
  // Add a trailing slash to the directory path.
  //
  // On Windows, if there's a file and a directory with the
  // same name (e.g `C:\MyFolder\foo` and `C:\MyFolder\foo.exe`),
  // when executing shell.openItem(`C:\MyFolder\foo`) then the EXE file
  // will get opened.
  // We can avoid this by adding a final backslash at the end of the path.
  const pathname = __WIN32__ && !path.endsWith('\\') ? `${path}\\` : path

  shell.openPath(pathname).then(err => {
    if (err !== '') {
      log.error(`Failed to open directory (${path}): ${err}`)
    }
  })
}
```

**File:** app/src/ui/main-process-proxy.ts (L98-121)
```typescript
const UNSAFE_openDirectory = sendProxy('unsafe-open-directory', 1)

export async function showFolderContents(path: string) {
  const stats = await stat(path).catch(err => {
    log.error(`Unable to retrieve file information for ${path}`, err)
    return null
  })

  if (!stats) {
    return
  }

  if (!stats.isDirectory()) {
    log.error(`Trying to get the folder contents of a non-folder at '${path}'`)
    await _showItemInFolder(path)
    return
  }

  // On Windows and Linux we can count on a directory being just a
  // directory.
  if (!__DARWIN__) {
    UNSAFE_openDirectory(path)
    return
  }
```

**File:** app/src/ui/app.tsx (L3439-3445)
```typescript
  private showRepository = (repository: Repository | CloningRepository) => {
    if (!(repository instanceof Repository)) {
      return
    }

    shell.showFolderContents(repository.path)
  }
```
