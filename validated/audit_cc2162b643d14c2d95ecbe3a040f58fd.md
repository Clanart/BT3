Based on the investigation, the strongest analog to the seed bug's pattern (an attacker-controlled value that slips through a supposedly protective validation check, producing a corrupted downstream value) is a **gap in the branch-name validator used for the "Open in Desktop" deep-link flow**.

### Title
Deep-link `branch` parameter validator omits leading-dash check, allowing git-argument injection during automatic checkout - (File: `app/src/lib/sanitize-ref-name.ts`)

### Summary
The `x-github-client://openrepo/...` protocol handler accepts a `branch` query parameter directly from an attacker-crafted URL and validates it with `testForInvalidChars()` before using it to automatically run a `git checkout` against the user's local clone. That validator's regex checks for control characters, whitespace, `~^:?*[\|"<>`, `..`, leading/trailing dot, `.lock` suffix and trailing slash — but it does **not** reject a value that begins with `-`. The sibling function `sanitizedRefName()` in the very same file explicitly strips a leading `-`/`+` after applying the identical regex, which shows the maintainers are aware that leading dashes are dangerous for ref-name-derived values, yet this stripping/rejection step was never added to `testForInvalidChars()`.

### Finding Description
- `parseAppURL()` reads `branch` from the query string of an app-scheme URL and only rejects it via `testForInvalidChars(branch)`: [1](#0-0) 
- `testForInvalidChars` reuses `invalidCharacterRegex`, which has no leading-dash exclusion, unlike `sanitizedRefName`, which explicitly strips leading `-`/`+` via `.replace(/^[-\+]*/g, '')`: [2](#0-1) 
- The accepted branch string is passed unmodified through `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl`, which clones/opens the target repository and then calls `checkoutLocalBranch(repository, branchName)` with no further sanitization: [3](#0-2) 
- This is corroborated by the project's own unit tests, which only assert rejection for characters like `<>` — never for a leading `-`: [4](#0-3) 

Because the value is attacker-supplied through a URL the victim clicks (no local access, no credentials, no prior malware needed), and it reaches a `git` invocation, a branch string such as `--upload-pack=...` or other leading-dash values could be interpreted by the underlying `git` binary as an option rather than a ref name if the call site that ultimately shells out to `git checkout` does not defend the argument with a `--` separator. I could not fully confirm the exact argument construction inside `app/src/lib/git/checkout.ts` before running out of tool budget, so whether this specific sink is fully exploitable to argument-injection-based code execution (vs. just an unexpected `git checkout` outcome) remains **unverified** — this is the main uncertainty in this finding.

### Impact Explanation
If the checkout call site builds `git(['checkout', branchName], ...)` without a `--` separator (a well-known git argument-injection pattern), an attacker who gets a victim to click a deep link can force git to interpret the "branch" as a flag against the victim's local repository, potentially corrupting the checkout, discarding local changes, or (depending on the exact flag surface exposed by the git subcommand invoked) escalating to unexpected file writes. This matches the "attacker controls ... a link or deep link the user clicks" + "silent corruption of what the user commits" impact categories in scope. If the sink does properly guard with `--`, the practical impact is reduced to a validation-completeness bug with no direct exploit path.

### Likelihood Explanation
Likelihood is high for reaching the vulnerable code path itself: the URL is fully attacker-controlled, requires only a single click, and the flawed validator is the only gate before the value reaches a git operation. Likelihood of the maximum-severity outcome (argument injection to code execution) is unresolved due to the unverified sink implementation in `checkout.ts`.

### Recommendation
Extend `testForInvalidChars` (or a value derived by first passing through `sanitizedRefName` and comparing equality) to reject any branch value beginning with `-`, matching the same protection already applied in `sanitizedRefName`. Independently, verify/harden every git invocation that consumes deep-link-provided branch names (e.g., in `app/src/lib/git/checkout.ts`) to pass user-controlled ref names after a literal `--` separator, so that even if a leading `-` slips through validation it cannot be interpreted as a flag by `git`.

### Proof of Concept
```
x-github-client://openrepo/https://github.com/owner/repo?branch=--upload-pack%3Dtouch%20%2Ftmp%2Fpwned
```
1. Victim has GitHub Desktop installed and clicks the link (e.g., embedded in a webpage/README/chat).
2. `parseAppURL` accepts the URL because `testForInvalidChars('--upload-pack=touch /tmp/pwned')` returns `false` (no disallowed characters, no leading-dash check).
3. `openBranchNameFromUrl` opens/clones `owner/repo` and calls `checkoutLocalBranch` with the unsanitized string.
4. If the underlying git invocation lacks a `--` separator before the ref argument, git parses `--upload-pack=...` (or similar dash-prefixed value) as an option instead of a branch name — the exact resulting behavior depends on the specific git subcommand/flags reachable, which requires further verification of `app/src/lib/git/checkout.ts`.

### Citations

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

**File:** app/src/lib/sanitize-ref-name.ts (L1-15)
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
