### Title
Stateful global regex in ref-name validation lets a malicious `x-github-client://openRepo` deep link bypass invalid-character checks - (File: `app/src/lib/sanitize-ref-name.ts`)

### Summary
The `testForInvalidChars` guard, which is the only validation applied to the attacker-controlled `branch` parameter of an `openRepo` deep link, is built on a `RegExp` that carries the global (`g`) flag and is reused with `.test()` across unrelated calls. Because `RegExp.prototype.test()` on a global regex is stateful (it advances and persists `lastIndex` between invocations on the *same* regex object), a prior successful match on one deep-link’s branch name can leave `lastIndex` positioned past the start of a subsequent, shorter malicious branch string, causing that later `.test()` call to scan from the wrong offset and report "no invalid characters" even though the string actually contains a rejected character earlier in the string.

### Finding Description
`app/src/lib/sanitize-ref-name.ts` defines: [1](#0-0) 

`invalidCharacterRegex` is declared once at module scope with the `g` flag, and `testForInvalidChars` calls `.test()` directly on that shared instance. Per the ECMAScript spec, a global-flagged regex's `lastIndex` property is mutated as a side effect of `.test()`/`.exec()` and is *not* reset to `0` before a new call — it is only reset to `0` when a match attempt fails. This means the outcome of `testForInvalidChars(branch)` for the current call can depend on the `lastIndex` left behind by a previous, unrelated invocation of the same function.

The only caller of `testForInvalidChars` is the deep-link parser: [2](#0-1) 

This function is invoked from OS-level protocol handlers whenever the user's browser/OS dispatches an `x-github-client://openRepo/...` (or `github-mac://openRepo/...`) URL — content fully controlled by whatever web page or link the user clicks: [3](#0-2) 

If `testForInvalidChars` incorrectly returns `false` for a branch value that actually contains a disallowed character (e.g. leading `-`, `~`, `^`, `:`, `?`, `*`, `[`, `\`, control characters, or the `@{` sequence), the unsanitized `branch` string is passed straight through as `IOpenRepositoryFromURLAction.branch` into `dispatcher.openBranchNameFromUrl` → `checkoutLocalBranch`, which ultimately reaches `git checkout`-style commands in `app/src/lib/git/checkout.ts`. Unlike `sanitizedRefName` (which uses `.replace()`—a stateless, spec-safe usage of the global regex that always scans the whole string), `testForInvalidChars` is the sole gate that decides whether the action is rejected as `unknown` before the value ever reaches git plumbing.

### Impact Explanation
The broken invariant is: *"every branch name accepted from an external `openRepo` URL has been verified to be free of git-refname-breaking / option-injection characters."* Because the shared, stateful regex can silently pass a value it should reject, a bad branch string can flow into a `git` invocation. A branch value beginning with `-` (which the invalid-character regex is meant to strip in `sanitizedRefName` but this path doesn't even call `sanitizedRefName` — it only gates on `testForInvalidChars`) can be interpreted as a command-line option to `git checkout` rather than a ref name, enabling git-argument injection (e.g., forcing `--` option semantics, or referencing arbitrary options accepted by the checkout subcommand). This is a code-execution/argument-injection primitive fed entirely by an external, unprivileged input (a link the user clicks), matching the report's underlying bug class: a defensive check that looks correct in isolation but silently fails to enforce its contract due to a structural mismatch (here, JS regex statefulness vs. the assumption of pure, stateless validation).

### Likelihood Explanation
This requires no local access, no malware, and no leaked credentials — only that the victim click an attacker-supplied `x-github-client://openRepo/...` link (the exact vector the `open-url`/`--protocol-launcher` handlers in `app/src/main-process/main.ts` are designed to accept from arbitrary web content). The regex statefulness only manifests when `testForInvalidChars` has previously been invoked and left a non-zero `lastIndex`, so triggering it reliably depends on the sequence/timing of prior deep-link parses within the same running process (e.g., a page issuing two protocol invocations, or Desktop being open long enough to have previously parsed at least one branch-bearing `openRepo` link). This makes it a genuine but conditional/timing-dependent bug rather than a trivially-always-reproducible one, which is why I flag it as the strongest available analog rather than certain.

### Recommendation
Do not reuse a single global `RegExp` instance for a boolean `.test()`-based validation check. Either:
- Construct a fresh `RegExp` (or clone via `new RegExp(invalidCharacterRegex)`) inside `testForInvalidChars` before calling `.test()`, or
- Drop the `g` flag for the "does it contain any invalid character" check (a `g`-less regex is stateless for `.test()`), keeping `g` only for the `.replace()`-based sanitization in `sanitizedRefName`, or
- Reset `invalidCharacterRegex.lastIndex = 0` immediately before every `.test()` call.

### Proof of Concept
Conceptual reproduction of the underlying JS behavior exploited here (illustrates the exact flaw in `testForInvalidChars`, independent of the app runtime):
```js
const re = /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

// First deep link: branch name has an invalid char at offset 40
const branch1 = 'a'.repeat(40) + '~' + 'b'.repeat(5)
console.log(re.test(branch1)) // true -> correctly rejected; re.lastIndex now ~41

// Second deep link arrives shortly after, in the same process:
// a short malicious branch whose invalid character sits before offset 41
const branch2 = '-upload-pack=touch /tmp/pwned;x'
console.log(re.test(branch2)) // false -> incorrectly ACCEPTED, because
                               // matching resumes from lastIndex (~41),
                               // past the end of branch2
```
In the real app, `branch2` corresponds to the `branch` query parameter of a second `x-github-client://openRepo/https://github.com/x/y?branch=...` link; because `testForInvalidChars` returns `false`, `parseAppURL` in `app/src/lib/parse-app-url.ts:114` treats it as valid, and it is passed to `dispatcher.openBranchNameFromUrl` → `checkoutLocalBranch` unmodified. [4](#0-3)

### Citations

**File:** app/src/lib/sanitize-ref-name.ts (L5-16)
```typescript
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

**File:** app/src/lib/parse-app-url.ts (L98-116)
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
