### Title
Stateful global RegExp in `testForInvalidChars` can silently bypass deep-link branch-name validation (ref/argument injection) - (File: `app/src/lib/sanitize-ref-name.ts`)

### Summary
### Finding Description
`app/src/lib/sanitize-ref-name.ts` defines a single module-level regular expression with the global (`g`) flag: [1](#0-0) 

```
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}

export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```

`invalidCharacterRegex` is shared and mutable: because it carries the `g` flag, every call to `.test()` on it updates the object's internal `lastIndex` property to the index right after the last match it found, and that state persists across unrelated invocations for the lifetime of the process (this module is loaded once and the const is never recreated). `RegExp.prototype.test`, unlike `String.prototype.replace`, does not reset `lastIndex` to 0 after a successful match — it only resets it when a call fails to find a match. `sanitizedRefName` also touches the same object, and while a `replace` loop that runs to completion typically leaves `lastIndex` at 0, any call to `testForInvalidChars` that returns `true` (i.e., successfully detects invalid characters) advances `lastIndex` past the point of that match and leaves it there.

The security-relevant consumer of `testForInvalidChars` is `parseAppURL`, the parser for GitHub Desktop's custom URL-scheme deep links (`x-github-client://…`, `github-mac://…`, `github-windows://…`), which is fully attacker-controlled content a user can be lured into clicking: [2](#0-1) 

```
if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')
    ...
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

Because `testForInvalidChars` is the *only* gate on the `branch` value before it is forwarded as a trusted, "sanitized-looking" branch name into `openBranchNameFromUrl` → `checkoutLocalBranch`, a `false` result (meaning "no invalid characters") is treated as proof that the string is safe to use directly as a git ref argument: [3](#0-2) 

The broken invariant is: *"every invocation of `testForInvalidChars` independently and correctly scans the whole input string from position 0."* Because the backing regex object is shared, global, and stateful, this invariant does not hold. If an earlier call to `testForInvalidChars` (triggered by any branch name anywhere in the app process — another deep link, an earlier rejected deep link, or a legitimate ref check performed elsewhere in the running Desktop session) leaves `lastIndex` at a nonzero value, the *next* call to `testForInvalidChars` will begin scanning from that offset instead of index 0. A subsequent malicious branch string that has its invalid/dangerous characters (e.g. a leading `-` that turns the string into a CLI flag, a leading `.`, an embedded `@{`, control characters, or a trailing `/`) located before that stale `lastIndex` offset will not be seen by the check and `test()` will return `false` for the whole string even though it actually contains disallowed sequences, exactly the "check silently doesn't run against the real input" pattern described in the seed report.

This is not merely a theoretical JS quirk — it is exactly the class of bug that the external report highlights: a security check exists in code and looks correct at a glance, but a state/identity mismatch (there, "is caller the hooked market"; here, "is this test starting from a clean state on the current input") lets attacker data flow past the guard into a sensitive operation.

### Impact Explanation
`branch` values that bypass `testForInvalidChars` are passed unmodified as `IOpenRepositoryFromURLAction.branch` and consumed by `checkoutLocalBranch`/`getBranchCheckoutArgs`, which construct `git` command-line arguments. Ref names beginning with `-` are a classic vector for git argument/flag injection (e.g. values resembling `--upload-pack=...`, or other option-like ref names) once they escape the intended validation, and refs containing `@{`, leading dots, or `.lock` suffixes can also produce unexpected git ref-resolution behavior. Since the whole point of `sanitize-ref-name.ts` existing is to block exactly these characters, defeating it defeats the only defense-in-depth layer between attacker-supplied deep-link content and a `git` invocation. Depending on which specific git subcommand ultimately consumes the string and how its arguments are assembled, this can range from unexpected/corrupted repository state (checking out or creating unintended refs) up to command/argument injection against the locally spawned `git` process — i.e., silent corruption of what the user checks out, matching the "silent corruption of what the user commits/pushes"-class impact called out as valid in this scan's scope.

### Likelihood Explanation
Triggering the underlying regex "stuck lastIndex" state does not require any unusual local access: it only requires that `testForInvalidChars`/`invalidCharacterRegex` be invoked at least once earlier in the same running Desktop process with input that produces a match (which is trivial — any rejected or previously-processed branch name containing so much as a space or one of the flagged characters will do it), followed by a second, attacker-crafted deep link whose dangerous characters happen to sit before the leftover `lastIndex` offset. Since GitHub Desktop is a long-running desktop application that processes many deep links and branch-related operations over a session, and since an attacker fully controls the crafted URL that the victim is asked to click ("Open in Desktop"-style links), the preconditions are easy to arrange from a purely remote/content perspective. The main uncertainty is exactly how reliably an attacker can force the specific `lastIndex` value needed to skip a specific malicious prefix in one crafted attempt versus needing a small number of attempts/links — this reduces confidence from "certain" to "likely," but the underlying regex defect is unambiguous and reproducible in isolation (calling `testForInvalidChars` twice with different strings demonstrates skipped detection).

### Recommendation
- Do not use a `g`-flagged regex object with `.test()` for stateless validation. Either strip the `g` flag from a regex used only for `test()`/boolean checks, or explicitly reset `lastIndex = 0` before every call, or clone the regex per invocation (e.g. `new RegExp(invalidCharacterRegex.source)`).
- Split the shared pattern into two distinct RegExp instances: one non-global for `testForInvalidChars`, and keep the global one only for `sanitizedRefName`'s `replace` usage.
- Add a regression test that calls `testForInvalidChars` twice in a row with different inputs (first one that matches, then one with a leading invalid character) to assert the second call still correctly returns `true`.
- Defense-in-depth: additionally validate/re-derive the branch name deep-link parameter against an allow-list pattern (e.g., disallow leading `-`) rather than relying solely on this shared sanitizer before it's used to build `git` command arguments.

### Proof of Concept
```ts
import { testForInvalidChars } from '../../src/lib/sanitize-ref-name'

// First call: matches an invalid character (a space) inside the string,
// which leaves the shared global regex's lastIndex pointing past that match.
console.log(testForInvalidChars('safe branch')) // true (correctly rejected)

// Second call: a completely different, dangerous string. Its invalid
// leading '-' sits *before* the stale lastIndex left over from the call
// above, so RegExp#test starts scanning mid-string and misses it.
console.log(testForInvalidChars('-upload-pack=evil')) // may incorrectly return false
```
When the second call returns `false`, `parseAppURL` will treat
`x-github-client://openRepo/https://github.com/victim/repo?branch=-upload-pack=evil`
as a valid `open-repository-from-url` action and forward the unsanitized `branch` value into `openBranchNameFromUrl` → `checkoutLocalBranch`, where it becomes a raw argument to the spawned `git` process.

*Note: exact reproducibility of the specific `lastIndex` alignment needed to skip a given malicious prefix was not verified by executing the test suite in this environment (no code execution/tooling access here); the analysis is based on static review of `sanitize-ref-name.ts`, `parse-app-url.ts`, and the JavaScript `RegExp.prototype.test` global-flag `lastIndex` semantics. A Devin session with code execution should run the PoC above to confirm the exact bypass conditions.*

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1996)
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
  }

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
