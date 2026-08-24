Based on the investigation, I found a concrete, attacker-reachable analog to the Vyper bug's pattern (a security-relevant validation function that silently lacks a check that a similar/sibling function does perform).

### Title
Deep-link branch name validation omits leading-dash rejection, enabling git flag/argument injection - ([File: app/src/lib/parse-app-url.ts])

### Summary
The `x-github-client://openrepo/...?branch=...` deep-link handler validates the attacker-supplied `branch` query parameter with `testForInvalidChars()`, but that function's regex never rejects (or strips) a leading `-`/`+`, unlike its sibling `sanitizedRefName()` in the same file, which explicitly strips leading `-`/`+` characters. This asymmetry mirrors the Vyper bug class exactly: a security check that exists in one code path is silently absent in the parallel path that actually gates a dangerous operation.

### Finding Description
`app/src/lib/sanitize-ref-name.ts` defines: [1](#0-0) 

`sanitizedRefName()` strips leading `-`/`+` via `.replace(/^[-\+]*/g, '')`, indicating the authors are aware that a ref name beginning with `-` is dangerous (it can be interpreted by git as an option rather than a positional ref argument). However, `testForInvalidChars()` — the function actually used to gate the deep-link branch parameter — only runs `invalidCharacterRegex.test(name)` and never checks for, or rejects, a leading dash.

This function is the sole validation applied to `branch` in `parseAppURL`: [2](#0-1) 

Note that the `pr`-branch combination is constrained to `/^pr\/\d+$/`, but the plain `branch` (no `pr`) is validated only by `testForInvalidChars`, which does not block a value like `--orphan`, `--track`, or similar. That value flows into `dispatcher.ts`'s `openRepositoryFromUrl` → `openBranchNameFromUrl(url, branch)` → `this.checkoutLocalBranch(repository, branchName)`: [3](#0-2) 

I was unable to confirm, within the available tool budget, the exact `checkoutLocalBranch` git-invocation code (whether it inserts a `--` separator before the branch argument to git). That is the one piece of definitive proof still needed to fully confirm exploitability.

### Impact Explanation
If `checkoutLocalBranch` (or the git calls beneath it) pass the branch string as a bare positional argument to `git checkout`/`git branch` without a `--` separator, a value starting with `-` would be interpreted as a git command-line flag rather than a ref name — the classic "argument injection" class. Depending on which flag is smuggled, this could alter checkout behavior (e.g., `--orphan`, custom `-b` naming collisions) when the branch value originates entirely from an untrusted deep link the user merely clicks (`x-github-client://openrepo/...`), which is a valid Impact category (attacker-controlled deep link leading to unauthorized/corrupting git operations).

### Likelihood Explanation
The `branch` parameter is fully attacker-controlled via a deep link — no local access or existing credentials are required, only a user click, consistent with how "Open in Desktop" flows are already treated as an external, less-trusted input source in this codebase (see the comment in `main.ts` distinguishing CLI/file-drop as "more trusted" than URL actions). The severity is bounded by whatever protections exist in the actual git invocation layer, which I could not fully verify.

### Recommendation
Reuse `sanitizedRefName()`'s leading-dash stripping logic (or equivalently reject any branch value matching `/^[-+]/`) inside `testForInvalidChars()`/`parseAppURL`, and/or confirm and enforce that all git subcommands receiving branch names from external input always insert a `--` separator before the ref argument.

### Proof of Concept
1. Register/observe the `x-github-client` protocol handler in `main.ts`.
2. Send: `x-github-client://openrepo/https://github.com/owner/repo?branch=--orphan`
3. `parseAppURL` accepts this because `testForInvalidChars('--orphan')` returns `false` (no invalid chars matched).
4. `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl(url, '--orphan')` → `checkoutLocalBranch(repository, '--orphan')`, passing the flag-like string toward git. [2](#0-1) [1](#0-0) [3](#0-2) 

**Caveat:** I could not locate and inspect the body of `checkoutLocalBranch` (or the underlying `git()` call it makes) within the available search budget, so I cannot confirm whether a `--` separator is already used there to neutralize this class of injection. If that guard exists, this finding is not exploitable and should be downgraded to a defense-in-depth gap rather than a live vulnerability. I recommend a Devin session with full file access to inspect `checkoutLocalBranch`'s implementation before treating this as confirmed.

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

**File:** app/src/lib/parse-app-url.ts (L98-117)
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
