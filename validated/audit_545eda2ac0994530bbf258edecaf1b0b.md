Based on the code I was able to review, the HackerOne report is fundamentally a "DoS/rate-limit-only" and unauthenticated-enumeration finding, which is explicitly out of scope per this task's validity rules. I looked instead for an analog matching the allowed classes (attacker-controlled deep link / remote / API object leading to code execution, unauthorized file access, credential exfiltration, or silent corruption of commits), focusing on the `x-github-client://openRepo` deep-link handler in `app/src/lib/parse-app-url.ts`, since that is the clearest "attacker controls a link the user clicks" surface in Desktop.

### Title
Deep-link `branch` parameter allows git option/argument injection into `checkout` - (File: app/src/lib/sanitize-ref-name.ts, app/src/ui/dispatcher/dispatcher.ts)

### Summary
The `x-github-client://openRepo/...?branch=<value>` deep link is parsed by `parseAppURL` and validated only against `testForInvalidChars`, which rejects control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix and trailing slash — but does **not** reject a leading hyphen `-`. [1](#0-0) [2](#0-1) 

This branch value flows unmodified into `openBranchNameFromUrl`, which clones/opens the target repo and then calls `this.checkoutLocalBranch(repository, branchName)`. [3](#0-2) 

### Finding Description
`testForInvalidChars` is the sole gate applied to a branch name supplied entirely by whoever crafts the `openRepo://` URL (a malicious webpage, email, or README link). Because the regex does not exclude a leading `-`/`--`, a value such as `--orphan=evil` or other double-dash git options passes validation untouched. If the branch string is ultimately passed as a single positional argument to a `git checkout` invocation, git itself will interpret a leading-hyphen string as an option rather than a ref name — a classic "argument injection via unsanitized CLI parameter" pattern. This is the same broken-invariant idea as the HackerOne report (unsanitized/attacker-supplied input reaching a sensitive backend operation with no allow-listing), just relocated from an HTTP endpoint to Desktop's local git invocation.

I was not able to fully verify, within the tool budget of this session, the exact argv construction inside `app/src/lib/git/checkout.ts` (i.e., whether the branch name is preceded by a `--` separator before being handed to the git spawn call). This is the key fact that determines whether the injection is actually exploitable at the git-CLI boundary, so it must be confirmed before treating this as a confirmed, fully-reachable vulnerability.

### Impact Explanation
If the branch string does reach `git checkout` without a `--` separator, an attacker-controlled deep link could force git to interpret it as an option (e.g., forcing branch creation semantics, altering checkout behavior, or other option-dependent side effects), which would corrupt what the user believes they checked out/committed without their awareness — matching the "silent corruption of what the user commits" impact class.

### Likelihood Explanation
Requires only that a user click a specially crafted `x-github-client://openRepo/<url>?branch=<payload>` link; no local access, credentials, or additional interaction beyond the normal "Open in Desktop" flow is needed. This is a low-friction, remotely triggerable path if the underlying git call lacks argument-separator protection.

### Recommendation
- Reject branch names that start with `-` in `testForInvalidChars`/`sanitizedRefName` (mirroring the check that is already present for `--` handling elsewhere in the codebase, e.g. `sanitizedRefName`'s trailing regex `.replace(/^[-\+]*/g, '')` should also gate the *validating* function, not just the *sanitizing* one). [4](#0-3) 
- Ensure every git invocation that takes a user/URL-supplied ref places a literal `--` before the ref argument, so git cannot interpret it as an option regardless of upstream validation gaps.

### Proof of Concept
1. Host or send a link: `x-github-client://openRepo/https://github.com/some/repo?branch=--orphan%3Devil`.
2. User clicks it; `parseAppURL` accepts the branch value because `testForInvalidChars` does not flag a leading `-`. [1](#0-0) 
3. `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl` → `checkoutLocalBranch(repository, branchName)` is invoked with the unsanitized value. [3](#0-2) 
4. Whether this produces attacker-controlled git behavior depends on the exact argv assembly in `app/src/lib/git/checkout.ts`, which I could not confirm in this session — this should be verified directly (i.e., check for a `--` separator before the ref argument in the `git(['checkout', ...])`-style call) before treating this as a confirmed exploit chain.

**Confidence caveat:** Unlike the input-validation gap (confirmed by reading `sanitize-ref-name.ts` and `parse-app-url.ts` directly), the actual exploitability at the git-CLI layer depends on code (`app/src/lib/git/checkout.ts`) that I was unable to inspect before running out of tool iterations. If that file already inserts a `--` separator or otherwise quotes the ref, this finding would be a defense-in-depth gap rather than an exploitable vulnerability, and the analog would then be weak. A Devin session with full file access should confirm this before treating it as validated.

### Citations

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
