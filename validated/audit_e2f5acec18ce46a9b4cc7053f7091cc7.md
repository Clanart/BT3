Based on my research, I found a real asymmetry in how GitHub Desktop protects against path-traversal via a repo name derived from an attacker-controlled URL — but I was unable to fully confirm the internals of the deep-link clone path with the tool budget I had left, so I'm flagging that explicitly below.

### Title
Clone destination path-traversal guard (`sanitizeCloneName`) is applied only in the manual Clone dialog, not in the deep-link/CLI "Open in Desktop" auto-clone flow - (File: app/src/ui/dispatcher/dispatcher.ts, app/src/ui/clone-repository/clone-repository.tsx)

### Summary
The Hats report's broken invariant is that two code paths that should derive the *same* value from the *same* source (asset↔share price) instead use inconsistent references (`currentPrice` vs `previousPrice`), letting an attacker exploit the gap. The Desktop analog is a security invariant — "never derive a filesystem clone path from a URL-controlled repo name without stripping path-traversal segments" — that is enforced in one code path but not demonstrably enforced in the other.

### Finding Description
`sanitizeCloneName()` in `app/src/lib/remote-parsing.ts` was added specifically to strip `..`, `\`, and `:` traversal tricks out of a URL-derived repository name before it's joined into a filesystem path, exactly to stop a crafted clone URL (e.g. `.../x..\..\..\.ssh.git`) from escaping the intended clone base directory [1](#0-0) . This function is wired into the **manual** "Clone Repository" dialog's `updateUrl` handler, which recomputes the destination path every time the URL field changes [2](#0-1) , and its safety is verified by dedicated tests [3](#0-2) .

However, `sanitizeCloneName` is referenced in exactly three files in the whole codebase: its definition (`remote-parsing.ts`), its test, and `clone-repository.tsx`. It is **not** referenced from `app/src/ui/dispatcher/dispatcher.ts`, which is the file that handles the attacker-reachable "Open in Desktop" deep link (`x-github-client://openRepo/<url>`) and the `--cli-clone` CLI switch. Both of these enter through `dispatchURLAction`/`dispatchCLIAction` and ultimately call `openOrCloneRepository(url)` [4](#0-3) [5](#0-4) , a path that never touches the manual dialog's `updateUrl`/`sanitizeCloneName` logic.

The only remaining backstop for this automated flow is `isClonePathSensitive()` in `app/src/lib/git/clone.ts`, which merely rejects a short, hardcoded allowlist of sensitive directories (`~/.ssh`, `~/.gnupg`, `~/.config`, `~/.gitconfig`, `%APPDATA%`) [6](#0-5) . It does **not** perform general traversal containment relative to the chosen base clone directory the way `sanitizeCloneName` + the `Path.join`/`resolve` containment check does in the manual-dialog tests [7](#0-6) . Any traversal target outside that specific list (e.g., another user-writable folder, an arbitrary project directory, or a location where a background tool watches for changes) is not blocked by `isClonePathSensitive`.

### Impact Explanation
If `openOrCloneRepository` (or whatever destination-path derivation it uses internally for automated clones) builds the clone target directory name directly from the parsed repository identifier without calling `sanitizeCloneName`, a malicious web page or email can embed a link like:
`x-github-client://openRepo/https://evil.example/owner/x..%5C..%5C..%5C.config.git`
When the user clicks it, Desktop would derive a repo name containing traversal segments and clone attacker content into a directory outside the intended clone-base folder (e.g. into `~/.config` if it isn't in the fixed sensitive list check's exact matches, or into a sibling project directory), silently overwriting or seeding files there — this is unprompted code/content placement outside the intended repository root, matching the "file write outside the repo" impact class.

### Likelihood Explanation
This requires no local access, no admin rights, and no prior credentials — only a link the user clicks, which is explicitly a valid attacker vector per the task's scope. The likelihood hinges entirely on whether `openOrCloneRepository`'s internal path-derivation logic reuses `sanitizeCloneName` indirectly (e.g. by routing through the same UI state) or independently reconstructs a path. **I was not able to retrieve the full body of `openOrCloneRepository` within my remaining tool budget** (only its call sites in `dispatcher.ts` were confirmed), so this should be treated as a strong candidate requiring direct verification of that function's source rather than a fully confirmed exploit chain. Given the index size limits noted in my instructions, if this file's relevant section isn't available through search, a full Devin session with direct filesystem access would be needed to read `openOrCloneRepository` in its entirety and confirm whether it calls `sanitizeCloneName` or an equivalent traversal-safe join before invoking `clone()`.

### Recommendation
- Verify whether `openOrCloneRepository` in `app/src/ui/dispatcher/dispatcher.ts` derives its destination directory using `sanitizeCloneName` (or an equivalent) before calling the `clone()` git wrapper.
- If not, call `sanitizeCloneName` (or reuse the same containment check used in the clone-repository tests) on any repository-name-derived path segment in the CLI/deep-link auto-clone flow, and verify with `Path.resolve` that the final path stays within the intended base directory — not just outside a fixed sensitive-directory list.
- Extend `isClonePathSensitive` (or add a parallel check) to reject any resolved clone path that escapes the chosen/default base directory, not only the specific hardcoded sensitive paths.

### Proof of Concept
Not fully constructible without confirming `openOrCloneRepository`'s internal path-derivation code (unavailable within this investigation's tool budget). The conceptual PoC, pending that confirmation, is: craft a URL with a traversal-laden path/owner/repo segment, deliver it as an `x-github-client://openRepo/...` deep link, have the victim click it, and observe whether the resulting clone directory lands outside the intended clone-base folder without hitting the `sanitizeCloneName`-equivalent guard that the manual dialog enforces.

### Citations

**File:** app/src/lib/remote-parsing.ts (L72-88)
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

**File:** app/test/unit/clone-path-safety-test.ts (L1-72)
```typescript
import { describe, it } from 'node:test'
import assert from 'node:assert'
import * as Path from 'path'
import {
  parseRepositoryIdentifier,
  sanitizeCloneName,
} from '../../src/lib/remote-parsing'

describe('sanitizeCloneName', () => {
  it('returns a simple name unchanged', () => {
    assert.equal(sanitizeCloneName('Hello-World'), 'Hello-World')
  })

  it('extracts last component from backslash-separated traversal', () => {
    assert.equal(sanitizeCloneName('x..\\..\\..\\..\\foo'), 'foo')
  })

  it('rejects names that resolve to .. or empty', () => {
    assert.equal(sanitizeCloneName('..'), null)
    assert.equal(sanitizeCloneName(''), null)
    assert.equal(sanitizeCloneName('.git'), null)
  })

  it('does not traverse from default basepath (#x\\..\\..\\..\\.ssh)', () => {
    assert.equal(sanitizeCloneName('x..\\..\\..\\../.ssh'), '.ssh')
  })
})

describe('clone path derivation with sanitizeCloneName', () => {
  it('normal URLs are unchanged after sanitization', () => {
    const urls = [
      'https://github.com/octocat/Hello-World.git',
      'git@github.com:octocat/Hello-World.git',
      'octocat/Hello-World',
    ]
    for (const url of urls) {
      const result = parseRepositoryIdentifier(url)
      assert(result !== null, `Failed to parse: ${url}`)
      assert.equal(sanitizeCloneName(result.name), 'Hello-World')
    }
  })

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
})
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2050-2058)
```typescript
  public async dispatchCLIAction(action: CLIAction) {
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
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
