### Title
Incomplete sensitive-path denylist in `isClonePathSensitive()` allows cloning into other credential directories (`~/.aws`, `~/.docker`, `~/.netrc`, cloud CLI configs) - (File: `app/src/lib/git/clone.ts`)

### Summary
`isClonePathSensitive()` blocks a hard-coded, incomplete set of paths (home itself, `.ssh`, `.gnupg`, `.config`, `.config/git`, `.gitconfig`, plus Windows `APPDATA`/`APPDATA/gnupg`) but omits many other well-known credential-bearing locations such as `~/.aws`, `~/.docker`, `~/.netrc`, `~/.kube`, and various cloud CLI config directories. [1](#0-0) 

### Finding Description
`clone()` calls `isClonePathSensitive(path)` before invoking `git clone` and throws only if the resolved destination equals the home directory or one of the five explicitly-listed paths. [2](#0-1) [3](#0-2) 

The destination path itself is derived in the Clone dialog by joining the user-selected base directory with a name sanitized from the attacker-controlled repository URL via `sanitizeCloneName()`, which extracts the last path component of the URL-derived repo name (e.g. `.aws`, `.docker`, `.netrc`) and joins it onto the current path with `Path.join`. [4](#0-3) [5](#0-4) [6](#0-5) 

If the resulting joined path lands on `~/.aws`, `~/.docker`, `~/.netrc`, `~/.kube`, or a cloud-CLI config directory (e.g. `~/.aws/credentials`'s parent), `isClonePathSensitive()` returns `false` because none of those are in the denylist, so `clone()` proceeds to run `git clone` into that location. [7](#0-6) 

### Impact Explanation
This is a real but narrow gap in the denylist: the mechanism (URL → `sanitizeCloneName` → `Path.join` with the current base path → `clone()`) only produces a path exactly equal to `home/.aws` (or similar) if the user's currently-selected clone base directory in the dialog is the home directory itself. The application's default base directory is `~/Documents/GitHub` (via `getDefaultDir()`), not home, so reaching `~/.aws` requires the user to have separately navigated/typed the home directory as their clone location. [8](#0-7) 

Additionally, `git clone` itself refuses to clone into a non-empty existing directory, so if `~/.aws` (or `~/.docker`, etc.) already exists with real credential files, `git clone` will simply fail — the attacker cannot overwrite existing credential files this way. The practical impact is limited to creating/populating these directories when they don't yet exist, which is a file write outside the intended repo folder but does not directly exfiltrate or overwrite existing credentials.

### Likelihood Explanation
Low-to-moderate. It requires (1) the victim to manually set the clone destination to their home directory (an unusual but not impossible user action, e.g. via the "Choose..." folder picker) and (2) the crafted repository URL to sanitize to a name matching an unlisted sensitive directory such as `.aws`, `.docker`, `.netrc`, or `.kube`. It does not work against the default clone location, and cannot overwrite pre-existing credential files due to git's own empty-directory requirement.

### Recommendation
Expand the `sensitiveLocations` list in `isClonePathSensitive()` (app/src/lib/git/clone.ts:24-38) to include additional common credential/config directories, e.g. `~/.aws`, `~/.docker`, `~/.netrc` (as a file, block exact match too), `~/.kube`, `~/.azure`, `~/.config/gcloud` (already covered under `.config`), `~/.docker/config.json`'s parent, and any OS-specific credential-manager directories. Consider switching from an explicit denylist to a broader heuristic, e.g. blocking any dotfile/dot-directory directly under the home directory unless explicitly allow-listed, which would close this class of gap more robustly than continuing to enumerate individual tool directories.

### Proof of Concept
1. In the Clone Repository dialog, use "Choose…" to set the local path to the user's home directory (e.g. `/Users/victim` or `C:\Users\victim`).
2. Enter a URL such as `https://evil.example.com/owner/.aws.git` (or `https://evil.example.com/owner/.docker.git`).
3. `parseRepositoryIdentifier` extracts `name = ".aws"`; `sanitizeCloneName(".aws")` returns `.aws` unchanged (no `.git` suffix, not `.`/`..`). [4](#0-3) 
4. `updateUrl()` joins the home-directory base path with `.aws`, producing `~/.aws` as the clone destination. [5](#0-4) 
5. `clone()` calls `isClonePathSensitive("~/.aws")`; since `.aws` is not in `sensitiveLocations`, the function returns `false` and `git clone` proceeds (assuming `~/.aws` does not already exist or is empty). [9](#0-8)

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
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

**File:** app/src/lib/git/clone.ts (L74-79)
```typescript
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }
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

**File:** app/src/ui/lib/default-dir.ts (L1-12)
```typescript
import * as Path from 'path'
import { getDocumentsPath } from './app-proxy'

const localStorageKey = 'last-clone-location'

/** The path to the default directory. */
export async function getDefaultDir(): Promise<string> {
  return (
    localStorage.getItem(localStorageKey) ||
    Path.join(await getDocumentsPath(), 'GitHub')
  )
}
```
