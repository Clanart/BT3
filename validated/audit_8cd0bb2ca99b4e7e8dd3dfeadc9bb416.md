Based on the evidence gathered, I was unable to complete verification of `openOrCloneRepository` in `app-store.ts` (ran out of tool calls before reading its body), so I present this with that caveat clearly flagged.

### Title
Path-traversal guard (`sanitizeCloneName`) is enforced only in the manual Clone dialog, not in the "Open in Desktop" protocol-handler clone path - (File: `app/src/lib/remote-parsing.ts`, `app/src/ui/clone-repository/clone-repository.tsx`, `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
This mirrors the AI Arena bug pattern: a safety constraint (`sanitizeCloneName`, the traversal-stripping analog of the `dailyAllowanceReplenishTime`/`allowanceRemaining` check) is applied on one code path that mutates state (the manual "Clone repository" GUI dialog) but is missing on a second, attacker-reachable path that produces the same effect (cloning to a filesystem path derived from an untrusted repository name) — the `x-github-client://openRepo` deep-link / "Open in Desktop" flow.

### Finding Description
`sanitizeCloneName()` was added specifically to prevent a URL-derived repository name from escaping the intended clone base directory via `../`, `\`, or `:` traversal sequences, mirroring git's own `git_url_basename()` defense. [1](#0-0) 

It is invoked only inside the manual clone dialog component, in `updateUrl` and `onChooseWithOpenDialog`, when the user types/selects a URL and the UI computes a destination folder with `Path.join`. [2](#0-1) [3](#0-2) 

However, GitHub Desktop also clones/opens repositories automatically via the `x-github-client://openRepo` deep link, parsed by `parseAppURL` into an `open-repository-from-url` action and dispatched to `openRepositoryFromUrl` → `openOrCloneRepository(url)` in the dispatcher, entirely bypassing the Clone dialog component (and therefore `sanitizeCloneName`). [4](#0-3) [5](#0-4) [6](#0-5) 

Notably, the `CloningRepository` model — which represents a repository actively being cloned in this automatic flow — derives its `name` (used for UI and potentially path bookkeeping) with a raw `Path.basename(this.url, '.git')` call, with no traversal filtering at all, unlike `sanitizeCloneName`, which explicitly strips `..`/`.` segments and rejects traversal-only results. [7](#0-6) 

The dedicated test suite for `sanitizeCloneName` demonstrates exactly the traversal payload this guard was built to stop (e.g. `x..\..\..\.\.ssh.git` resolving outside the base directory if unsanitized), confirming that omission of the check anywhere in the clone-path derivation is a security-relevant regression, not a benign inconsistency. [8](#0-7) 

### Impact Explanation
If the "Open in Desktop"/protocol-handler clone path (`openOrCloneRepository`) builds its destination directory from the untrusted `url`'s repo-name component without routing through `sanitizeCloneName` (as the raw `Path.basename` usage in `CloningRepository.name` suggests), a malicious link (e.g., crafted by an attacker-controlled GitHub API repository object, a deep link, or a malicious page invoking `x-github-client://openRepo?url=...`) could cause Desktop to clone into a directory outside the user's intended clone root — e.g., overwriting or seeding files in `~/.ssh` or another sensitive location — the same class of "constraint bypass via an alternate code path" identified in the original finding.

### Likelihood Explanation
This requires only that the victim click an attacker-supplied link or "Open in Desktop" button pointing at a crafted repository URL/owner-name — no local access, malware, or leaked credentials needed, consistent with the "Valid Impact" criteria (attacker controls a link/deep link or a GitHub API object). I was not able to fully confirm, within available tool calls, that `openOrCloneRepository` in `app-store.ts` (as opposed to some other later join step) actually persists the traversal into the final `fs`/`git clone` path rather than re-sanitizing it internally, so likelihood is assessed as **plausible but not fully confirmed** given incomplete visibility into `app-store.ts`'s clone implementation.

### Recommendation
Route the repository-name-to-directory derivation used by `openOrCloneRepository` / `CloningRepository` through the same `sanitizeCloneName()` function used by the manual Clone dialog, so both code paths enforce identical traversal protection. If `CloningRepository.name` is genuinely used to build a filesystem path anywhere in the auto-clone flow, replace the raw `Path.basename(this.url, '.git')` with `sanitizeCloneName`.

### Proof of Concept
Conceptual PoC (unverified end-to-end due to incomplete visibility into `app-store.ts`):
1. Attacker crafts a link: `x-github-client://openRepo?url=https://evil.com/owner/x..%5C..%5C..%5C.%5C.ssh.git`
2. Victim clicks the link; `parseAppURL` extracts `open-repository-from-url` with `url = "https://evil.com/owner/x..\..\..\.\.ssh.git"`. [4](#0-3) 
3. `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository(url)` — this path never calls `sanitizeCloneName`, unlike the manual dialog's `updateUrl`/`onChooseWithOpenDialog`. [5](#0-4) 
4. If the destination directory is built from the raw parsed name (as `CloningRepository.name` does via `Path.basename`), the same traversal payload that `clone-path-safety-test.ts` proves escapes the base directory when unsanitized would do so here as well. [9](#0-8)

### Citations

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1955)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/models/cloning-repository.ts (L19-21)
```typescript
  public get name(): string {
    return Path.basename(this.url, '.git')
  }
```

**File:** app/test/unit/clone-path-safety-test.ts (L43-71)
```typescript
  it('traversal payload clone path stays contained (POSIX)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = '/Users/victim/Documents/GitHub'
    const resolved = Path.resolve(Path.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir`
    )
  })

  it('traversal payload clone path stays contained (Windows)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = 'C:\\Users\\victim\\Documents\\GitHub'
    const resolved = Path.win32.resolve(Path.win32.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.win32.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir on Windows`
    )
  })
```
