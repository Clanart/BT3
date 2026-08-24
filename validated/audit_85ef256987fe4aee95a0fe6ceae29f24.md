Based on the investigation, the strongest and most concrete analog is in the deep-link (`x-github-client://openRepo/...`) parser: unlike the `branch` parameter, the `filepath` parameter taken from an attacker-controlled URL is never validated before being forwarded to the rest of the app.

### Title
Unsanitized `filepath` parameter from `x-github-client://openRepo` deep links bypasses the traversal checks applied to `branch` - (File: app/src/lib/parse-app-url.ts)

### Summary
`parseAppURL` treats the `openRepo` deep-link action's query parameters asymmetrically: the `branch` value is passed through `testForInvalidChars` to reject shell/ref-breaking characters, but the `filepath` value taken from the exact same untrusted URL is returned completely unvalidated.

### Finding Description
`parseAppURL` extracts `pr`, `branch`, and `filepath` from the query string of an `x-github-client://openRepo/...` URL [1](#0-0) . The `branch` value is explicitly checked with `testForInvalidChars(branch)` and the action is discarded if it fails [2](#0-1) , but `filepath` is read straight from the query string with `getQueryStringValue(query, 'filepath')` and returned as-is in the `IOpenRepositoryFromURLAction` object with no format, character, or path-traversal validation [3](#0-2) [4](#0-3) . This value is fully attacker-controlled: it comes from a link (`https://github.com/.../open?...` → protocol handler URL) that a malicious repository owner or a phishing page can construct, matching the "link/deep-link the user clicks" attacker primitive named in the task's valid-impact list. The existing test suite confirms `filepath` is passed through untouched for arbitrary values including a path containing `/` segments, while confirming `branch` is rejected for invalid characters [5](#0-4) [6](#0-5) . The value then flows into `app/src/ui/dispatcher/dispatcher.ts`, which contains multiple references to `filepath` for the "open file after clone" flow [7](#0-6) , but I was not able to fully inspect how `dispatcher.ts` consumes this value within the available tool budget — this is the piece of the chain I could not verify directly and should be checked before treating this as a confirmed, exploitable primitive (does it get joined with the repo path via `Path.join` without a containment check, or passed to `shell.openItem`/`shell.showItemInFolder`, etc.).

### Impact Explanation
If `dispatcher.ts` joins the unsanitized `filepath` onto the freshly cloned repository's working directory (e.g. `Path.join(repoPath, filepath)`) to open a file after "Open in Desktop", a value like `../../../../../../Users/victim/.ssh/id_rsa` or `..\\..\\AppData\\Roaming\\...` would resolve outside the repository, and the app would open/read a file outside the intended clone directory in the user's editor/file viewer. This is directly analogous to the AI Arena mitigation's broken invariant — a value derived from attacker-controlled input (`dna` there, `filepath` here) is used for a security-relevant operation without being constrained to safe values, while a structurally identical sibling field (`branch`/other DNA inputs) *is* constrained. Compare with the parallel `sanitizeCloneName`/`isClonePathSensitive` guards that Desktop already applies to the analogous clone-path-derivation problem [8](#0-7) [9](#0-8)  — no equivalent guard exists for `filepath`.

### Likelihood Explanation
Likelihood cannot be confirmed as "High" without verifying the exact sink in `dispatcher.ts`; if the consumed `filepath` is only used relative to the repo directory with a `Path.resolve`+containment check (as is done for clone paths), the primitive is inert. Given that the codebase demonstrably applies containment checks for the structurally similar clone-path case but not for `filepath` at the parser layer, this warrants direct verification of the sink logic in `app/src/ui/dispatcher/dispatcher.ts`.

### Recommendation
Apply the same discipline used for `branch` and for clone-path derivation to `filepath`: validate that it is a relative path with no `..` traversal segments and no drive-letter/absolute-path prefix (mirroring `sanitizeCloneName`'s approach of extracting single safe components and rejecting traversal), and enforce that the resolved path stays within the cloned repository's working directory before any file-open/read/reveal operation is performed. Add unit tests in `app/test/unit/parse-app-url-test.ts` for traversal payloads in `filepath` analogous to the existing `branch` invalid-character tests.

### Proof of Concept
1. Attacker hosts (or has push/create access to) a public repository and crafts the URL:
   `x-github-client://openRepo/https://github.com/attacker/repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim clicks the link (e.g. embedded in a README's "Open in Desktop" badge or a phishing email).
3. `parseAppURL` accepts the URL because `branch=main` passes `testForInvalidChars`, and returns `filepath: '../../../../.ssh/id_rsa'` unmodified [10](#0-9) .
4. Desktop clones `attacker/repo` and — if `dispatcher.ts`'s "open file after clone" logic joins `filepath` onto the new repo path without containment enforcement — attempts to open the resolved path outside the repo.

This last step (confirming the exact join/open behavior in `dispatcher.ts`) needs direct code review before treating this as a fully confirmed vulnerability rather than a strong candidate based on the asymmetric validation observed in `parse-app-url.ts`.

### Citations

**File:** app/src/lib/parse-app-url.ts (L1-24)
```typescript
import * as URL from 'url'
import { testForInvalidChars } from './sanitize-ref-name'

export interface IOAuthAction {
  readonly name: 'oauth'
  readonly code: string
  readonly state: string
}

export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
```

**File:** app/src/lib/parse-app-url.ts (L98-124)
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
```

**File:** app/test/unit/parse-app-url-test.ts (L72-78)
```typescript
    it('returns unknown for invalid branch name', () => {
      // branch=<>
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=%3C%3E'
      )
      assert.equal(result.name, 'unknown')
    })
```

**File:** app/test/unit/parse-app-url-test.ts (L80-93)
```typescript
    it('adds file path if found', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=Octokit.Reactive%2FOctokit.Reactive.csproj'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/octokit/octokit.net')
      assert.equal(openRepo.branch, 'master')
      assert.equal(
        openRepo.filepath,
        'Octokit.Reactive/Octokit.Reactive.csproj'
      )
    })
```

**File:** app/src/lib/remote-parsing.ts (L88-115)
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
```

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
