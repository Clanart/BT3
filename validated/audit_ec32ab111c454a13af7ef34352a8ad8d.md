## Analog Found: Stateful Global RegExp in Deep-Link Branch Validation Allows Bypass — (File: `app/src/lib/sanitize-ref-name.ts`)

### Summary
The zkEVM bug is a validation routine that checks only *part* of a value (only `B0` of `B0–B7`), silently letting invalid inputs through. The Desktop analog is `testForInvalidChars` in `app/src/lib/sanitize-ref-name.ts`, which uses a **module-scoped `RegExp` with the `g` flag** and calls `.test()` on it. Because `RegExp.prototype.test` on a global regex mutates and retains `lastIndex` across calls, repeated invocations of this single shared regex object can silently skip the *beginning* of a later string, causing the validity check to return `false` (no invalid chars) even when the string actually contains disallowed ref characters.

### Finding Description
`invalidCharacterRegex` is declared once at module scope with the `g` flag: [1](#0-0) 

`RegExp.prototype.test()` on a `g`-flagged pattern advances `lastIndex` to the position after a match on success, and only resets it to `0` when a call finds **no** match. Since the same `invalidCharacterRegex` object is reused for every call to `testForInvalidChars`, the search position from one call carries into the next call on a *completely different string*. If a prior call matched near the end of its input (advancing `lastIndex` to a large offset), the next call — on a new, shorter string containing invalid characters near the start — begins scanning past those characters and can report "no invalid characters found," even though the string is unsafe.

This is used directly to gate untrusted, attacker-controlled input coming from the OS-level deep-link handler: [2](#0-1) 

The `branch` value originates from a `x-github-client://openrepo/...?branch=...` URL that any web page, email, or the GitHub API/website can cause the OS to hand to Desktop via `app.on('open-url', ...)` / `handleAppURL`: [3](#0-2) 

If `testForInvalidChars` returns a false negative due to stale `lastIndex`, the branch string is passed through untouched as `IOpenRepositoryFromURLAction.branch` and ultimately flows into `openBranchNameFromUrl` → `checkoutLocalBranch`: [4](#0-3) 

Unlike the sibling `filepath` handling in the same dispatcher function — which is defensively re-validated with `isAbsolute()` and `resolveWithin()` right before use — the `branch` value that passed (or bypassed) `testForInvalidChars` in `parseAppURL` is trusted for the rest of the pipeline with no second check: [5](#0-4) 

### Impact Explanation
A ref name that should have been rejected (containing control characters, `~^:?*[\|<>`, the `@{` sequence, `..`, a leading `-` making it look like a git CLI flag, etc.) can reach the checkout code path unmodified because the shared, stateful regex intermittently fails to flag it. Since the exact bypass condition depends on the `lastIndex` left over from the *previous* unrelated call (which could originate from any other deep link previously processed by the same running Desktop process, including one triggered by the attacker themselves as a priming step), this is a reachable, attacker-influenced state-corruption bug rather than a purely theoretical one. It matches the report's core theme — a range/validity check that is real code, looks correct, but structurally only covers part of the input space (here: part of the *string*, and only on some calls).

### Likelihood Explanation
The `g` flag + shared-instance + `.test()` reuse anti-pattern is a well-known JavaScript footgun and is exactly what's present here: a single module-level `invalidCharacterRegex` object used by a function called on every deep-link `open-repository-from-url` action for the lifetime of the process. An attacker who controls a repository/PR page that generates "Open in Desktop" links (or crafts one directly) can chain two link clicks (or automate a redirect chain) to (1) prime `lastIndex` with a first `branch=` value containing an invalid character near the end of a long string, then (2) send the real payload as a shorter string with the invalid character positioned before the primed `lastIndex`. This requires no local access, no malware, and no credentials — only that the victim click/open two attacker-supplied `x-github-client://` links, which is within the scope of the deep-link/API-object threat model given for this task.

### Recommendation
Do not call `.test()` (or `.exec()`) on a shared, module-scoped `RegExp` that has the `g` flag when the intent is a stateless "does this string contain X" check. Either:
- Reset `invalidCharacterRegex.lastIndex = 0` before every `.test()` call, or
- Construct a fresh `RegExp` instance per call, or
- Drop the `g` flag for the `test()`-only usage in `testForInvalidChars` (keep a separate global-flagged instance only for the `.replace()` usage in `sanitizedRefName`).

Additionally, add a defense-in-depth re-validation of `branch` immediately before it's used in `checkoutLocalBranch`/`_checkoutPullRequest`, mirroring the `isAbsolute`/`resolveWithin` re-check already done for `filepath`.

### Proof of Concept
```ts
import { testForInvalidChars } from './sanitize-ref-name'

// Call 1: invalid char '~' is near the end of a long string.
// .test() matches, and (due to the `g` flag) leaves lastIndex
// pointing near the end of THIS string on the shared regex object.
console.log(testForInvalidChars('a'.repeat(60) + '~')) // true, lastIndex ~= 61

// Call 2: a short, genuinely invalid branch name whose bad
// character sits BEFORE index 61. Because lastIndex from the
// previous call is already past this string's length, the
// underlying regex search starts beyond the string and finds
// no match — testForInvalidChars incorrectly returns false.
console.log(testForInvalidChars('~evil-branch')) // false (should be true)
```
Chained through `parseAppURL`, a second `x-github-client://openrepo/<url>?branch=~evil-branch` deep link processed right after the priming link would pass branch validation despite containing a disallowed ref character, and be forwarded to the checkout code path as if it were a validated branch name. [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/sanitize-ref-name.ts (L1-16)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}

/** Validate that a reference does not contain any invalid characters */
export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1975-1996)
```typescript
  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```
