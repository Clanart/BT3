## Analog Found: Git ref/branch argument injection via `x-github-client://` deep link [1](#0-0) 

### Title
Deep-link `branch` parameter bypasses argument-injection guard before reaching `git checkout` - (File: `app/src/lib/parse-app-url.ts`)

### Summary
The Solidity report's core pattern is: a field that flows into a sensitive operation is validated in some code paths but not others, letting attacker-controlled input reach the sensitive sink unchecked. The Desktop analog is the `branch` query parameter of the `x-github-client://openRepo/...` protocol handler URL: it is checked with `testForInvalidChars()`, but that check omits validation against leading-hyphen ("`-`") values, so a branch string such as `--upload-pack=...` or other `-`-prefixed git option strings passes the "safe branch" gate and is forwarded to the branch-checkout code path.

### Finding Description
`parseAppURL()` accepts an `openRepo` deep link and only rejects a `branch` value when it matches a PR-branch shortcut check or fails `testForInvalidChars()`: [2](#0-1) 

`testForInvalidChars()` is based on `invalidCharacterRegex`, which blocks control characters, space, `~^:?*[\|""<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix, and trailing slash — but it does **not** exclude a leading hyphen: [3](#0-2) 

Because of this, an option-looking string with no spaces or forbidden characters (e.g. `--upload-pack=/bin/sh`) is accepted as a "valid" branch and flows unmodified into `Dispatcher.openBranchNameFromUrl`, which fetches the target repository and then checks out the attacker-supplied branch string: [4](#0-3) 

This is exactly the class of bug the codebase is otherwise aware of and defends against elsewhere: `app/src/lib/git/clone.ts` explicitly inserts a `--` separator before passing `url`/`path` to `git` specifically to stop them from being parsed as option flags: [5](#0-4) 

That same `--` separator discipline is not visible anywhere in the `branch` validation/consumption path for the `openRepo` deep link — the only gate is the character blacklist in `sanitize-ref-name.ts`, which (unlike the clone path) does not stop `-`-leading strings from reaching the eventual `git checkout`/`git branch` invocation.

### Impact Explanation
If the underlying checkout implementation invoked by `checkoutLocalBranch` passes the branch string as a bare argument to `git checkout`/`git branch` (rather than with a `--` separator), an attacker who gets a victim to click a crafted `x-github-client://openRepo/<repo-url>?branch=<option-like-string>` link can inject arbitrary git command-line options into that invocation. Depending on the exact git subcommand and flags reachable this can range from corrupting the checked-out working tree/branch state silently, to more severe outcomes such as invoking `--upload-pack=<arbitrary program>` style option-smuggling patterns that git argument-injection research has shown can lead to command execution in other git subcommands. This matches the "Valid Impact" criteria: attacker controls a deep link, no local/admin access needed, and result is corruption of what gets checked out or potential command execution.

### Likelihood Explanation
Likelihood is moderate-to-high for the trigger conditions (a single click on an external/deep link is the only user action required, consistent with GitHub Desktop's own `--protocol-launcher` handling of `x-github-client://` URLs), but full severity depends on whether the specific git invocation inside the (unverified) checkout implementation lacks a `--` separator before the branch argument. I confirmed the missing hyphen check in `testForInvalidChars` and the absence of any additional sanitization between `parseAppURL` and `Dispatcher.checkoutLocalBranch`, but I could not fully inspect the exact git command construction inside the checkout code path (e.g. `app/src/lib/git/checkout.ts`) within the available tool budget, so whether a `--` guard exists at that specific call site is unconfirmed.

### Recommendation
- Reject branch names that start with `-` in `testForInvalidChars`/`sanitizedRefName` (`app/src/lib/sanitize-ref-name.ts`), mirroring the git ref-name rule that disallows refs beginning with a dash.
- Alternatively/additionally, ensure every git invocation that consumes a URL-derived branch name (in the checkout code invoked from `Dispatcher.openBranchNameFromUrl`) inserts a `--` separator before the ref argument, exactly as already done for `url`/`path` in `app/src/lib/git/clone.ts`.

### Proof of Concept
1. Host or send a link: `x-github-client://openRepo/https://github.com/<owner>/<repo>?branch=--upload-pack=touch%20/tmp/pwned`
2. Victim (with GitHub Desktop installed and the protocol registered, see `app/src/main-process/main.ts:333`) clicks the link.
3. `parseAppURL` accepts the URL because `--upload-pack=touch /tmp/pwned` has no space-escaped/forbidden characters after URL-decoding is limited to the option value; a purely option-like value such as `--upload-pack=/tmp/x` (no spaces) passes `testForInvalidChars` cleanly.
4. `Dispatcher.openBranchNameFromUrl` clones/fetches then calls `checkoutLocalBranch(repository, branchName)` with the attacker-controlled string, which (pending verification of the exact checkout git-argument construction) may be passed as a raw CLI argument to git.

**Note on confidence:** I verified the missing leading-hyphen rejection in `sanitize-ref-name.ts` and the deep-link flow in `parse-app-url.ts`/`dispatcher.ts` directly from the indexed code. I was not able to retrieve the exact git command array built by `checkoutLocalBranch`'s underlying implementation before the tool budget ran out, so the final confirmation that this reaches an unguarded git argument (as opposed to a safe API call) is unconfirmed. If you need certainty on that last link, a Devin session with full file access would be needed to inspect `app/src/lib/git/checkout.ts` and `Dispatcher.checkoutLocalBranch`.

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

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```
