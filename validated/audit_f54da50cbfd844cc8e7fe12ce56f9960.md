Based on my investigation, I found a concrete argument-injection analog in the deep-link → branch-checkout path.

### Title
Git argument injection via unsanitized `branch` query parameter in deep-link URLs leads to malicious checkout flag injection - (File: `app/src/lib/parse-app-url.ts`, `app/src/lib/git/checkout.ts`)

### Summary
`parseAppURL` validates the `branch` query parameter of `x-github-client://openrepo/...` deep links using `testForInvalidChars`, but that validator does not reject a leading `-`/`--`. The resulting attacker-controlled branch string is later placed as the **first** positional argument to `git checkout` (before the `--` separator), allowing an attacker-crafted deep link to inject a git command-line flag instead of a ref name.

### Finding Description
`parseAppURL` accepts an `openrepo` action with a `branch` parameter and only rejects it if `testForInvalidChars(branch)` matches: [1](#0-0) 

`testForInvalidChars` is backed by `invalidCharacterRegex`, which blocks control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix, and trailing slash — but it does **not** block a leading `-`: [2](#0-1) 

Note that `sanitizedRefName` (used elsewhere for user-created branches) explicitly strips leading `-`/`+` via `.replace(/^[-\+]*/g, '')`, but `testForInvalidChars`, used for the deep-link branch parameter, has no equivalent protection — the two functions share the same regex but only one performs the leading-dash strip.

The accepted branch value then flows through `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl` → checkout, ending in `checkoutBranch`, which builds the checkout argument list as: [3](#0-2) 

Critically, `branch.name` is placed **before** the `--` positional-argument separator (`[branch.name, ...(remote ? ['-b', name] : []), '--']`), so if `branch.name` begins with `-`, git parses it as a flag on the `git checkout` command rather than a ref name.

### Impact Explanation
This breaks the invariant that a deep-link-supplied `branch` string is only ever used as a git *ref name*. Because it is trusted as validated (having passed `testForInvalidChars`) but is not actually restricted from leading dashes, an attacker who gets a victim to click a crafted `x-github-client://openrepo/<url>?branch=--<flag>` link can inject arbitrary `git checkout` flags into the invocation run against the victim's freshly opened/cloned repository. Depending on which flag is injected (e.g., options that affect working-tree file writes, checkout of arbitrary paths, or other checkout-time hooks/behavior), this can result in unexpected file writes or corruption of the working tree contents the user believes they just checked out — i.e., silent corruption of what the user is about to commit, without any further unnatural steps beyond clicking a link.

### Likelihood Explanation
The attacker-controlled entry point is a `git remote`/deep-link parameter clicked by the user — exactly the class of "link the user clicks" input in scope. No local access, admin rights, prior malware, or leaked credentials are required; the victim only needs to click a link, which is the intended "Open in Desktop" flow. The existing validation (`testForInvalidChars`) gives a false sense of safety since it was clearly designed to sanitize the branch value but omits the leading-dash case that `sanitizedRefName` already handles elsewhere in the codebase, indicating this is a real gap rather than an intentional design choice.

### Recommendation
Apply the same leading-dash stripping/rejection used in `sanitizedRefName` to the deep-link branch validation in `parse-app-url.ts` (e.g., reject or strip any branch value starting with `-`), and/or ensure `getBranchCheckoutArgs` always places `branch.name` after a `--` separator (or a `--` before it too, e.g. `checkout -- <branch> ...`), similar to how `checkoutPaths` and `checkoutConflictedFile` already prefix `--` before user-influenced values.

### Proof of Concept
1. Craft a deep link: `x-github-client://openrepo/https://github.com/owner/repo?branch=--conflict=diff3`  (or any other `git checkout` flag not requiring an argument value that would need escaping).
2. `parseAppURL` accepts it because `testForInvalidChars('--conflict=diff3')` finds no matches under `invalidCharacterRegex` [4](#0-3) .
3. Victim clicks the link; Desktop dispatches `open-repository-from-url` → `openBranchNameFromUrl` → eventually `checkoutBranch(repository, branch, ...)` with `branch.name === '--conflict=diff3'`.
4. `getBranchCheckoutArgs` produces `['--conflict=diff3', '--']` as the args appended after `checkout`, so the executed command becomes `git checkout --conflict=diff3 --` instead of `git checkout <hash-or-name> --`, causing git to treat the attacker string as a flag rather than a ref [3](#0-2) .

**Unverified/uncertain aspects:** I was unable to locate and fully read `checkoutLocalBranch` in `app/src/ui/dispatcher/dispatcher.ts` due to running out of tool iterations, so I cannot confirm whether an intermediate step re-validates or re-derives `branch.name` before it reaches `checkoutBranch`, nor can I enumerate every `git checkout` flag that would be exploitable for maximal impact (some flags require additional arguments that could be absorbed by subsequent array elements). A Devin session with full file access would be needed to trace `checkoutLocalBranch`'s exact implementation and enumerate a maximal-impact flag payload.

### Citations

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

**File:** app/src/lib/git/checkout.ts (L24-36)
```typescript
function getCheckoutArgs(progressCallback?: ProgressCallback) {
  return ['checkout', ...(progressCallback ? ['--progress'] : [])]
}

async function getBranchCheckoutArgs(branch: Branch) {
  return [
    branch.name,
    ...(branch.type === BranchType.Remote
      ? ['-b', branch.nameWithoutRemote]
      : []),
    '--',
  ]
}
```
