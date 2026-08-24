## Title
Git argument/flag injection via attacker-controlled branch name in deep-link/PR checkout flow — placement of `--` after the untrusted ref instead of before it - (`app/src/lib/git/checkout.ts`)

## Summary
GitHub Desktop's `x-github-client://openRepo/...?branch=<name>` deep link (and the pull-request checkout flow) passes an externally-controlled branch/ref string into `git checkout` through `getBranchCheckoutArgs()`. The end-of-options marker `--` is appended *after* the branch name instead of *before* it, so a ref string that begins with `-` is parsed by git as a command-line flag rather than a literal ref, and the only validation performed on the deep-link value (`testForInvalidChars`) does not reject a leading dash.

## Finding Description
The URL handler parses `openRepo` deep links and only rejects a branch value if it matches `testForInvalidChars`: [1](#0-0) 

`testForInvalidChars` is backed by a regex that filters control characters, `~^:?*[\|""<>`, `@{`, consecutive/leading/trailing dots, `.lock` suffix and trailing slash — it never rejects a value that begins with `-`: [2](#0-1) 

A branch value that passes this check reaches `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl`, which forwards the attacker-supplied `branchName` verbatim to `checkoutLocalBranch`: [3](#0-2) 

Downstream, the shared checkout implementation builds the git argv as: [4](#0-3) 

Note that `branch.name` is placed *first* and the `--` end-of-options sentinel is appended *last*. The conventional/safe pattern for passing an untrusted ref/pathspec to git is `git checkout -- <name>` (options terminator **before** the untrusted value). Here it is inverted to `git checkout <name> ... --`, so if `branch.name` begins with `-` (e.g. `--orphan=x`, `-B`, `--detach`, `--recurse-submodules`, `--conflict=diff3`, etc.) git parses it as an option/flag rather than a literal ref name, because the `--` terminator has not yet been seen.

The existing test suite only proves that git rejects genuinely malformed refs like `..`: [5](#0-4) 

It does not cover a ref value starting with `-`, which is exactly the case `testForInvalidChars` fails to block and which the `--`-placement bug in `checkout.ts` fails to neutralize.

## Impact Explanation
This falls in the "attacker controls ... a link or deep link the user clicks" category. By convincing a user to click a crafted `x-github-client://openRepo/<url>?branch=<flag-like-value>` link, an attacker can cause Desktop to invoke `git checkout` with an attacker-chosen option instead of a plain ref argument. Depending on which flag is smuggled this can silently alter what gets checked out (e.g. `--orphan=<name>` creates an orphan branch, `-B`/`--force` reset an existing branch discarding local history, `--recurse-submodules`/`--no-recurse-submodules` change submodule state) — i.e. silent corruption of the repository state the user subsequently commits/pushes from, without any additional local access, admin rights, or prior compromise.

## Likelihood Explanation
The only gate between the untrusted URL query parameter and the vulnerable git invocation is `testForInvalidChars`, which does not consider leading `-` as invalid, and this is a single-click, no-account, no-local-access, no-social-engineering-beyond-a-normal-link scenario (Desktop already treats these `x-github-client://` URLs as first-class UX for "Open in Desktop" buttons on the web). The likelihood is limited by (a) the need for the crafted flag value to still resolve to something git accepts as an argument at the position it lands, and (b) I was unable to locate the definition of `checkoutLocalBranch` in the indexed code (only its two call sites in `dispatcher.ts` were found), so I cannot fully confirm whether it passes `branchName` through unmodified into the same `checkout.ts` code path or performs additional branch-name resolution/sanitization first. This is a real gap in my verification and should be checked directly in the repository before treating this as conclusively exploitable end-to-end.

## Recommendation
- In `getBranchCheckoutArgs` (`app/src/lib/git/checkout.ts`), move the `--` end-of-options marker to immediately follow the `checkout` subcommand (and any `-b`/`--progress` flags), before the branch name, e.g. `['checkout', '--progress', '-b', newName, '--', branch.name]`.
- Extend `testForInvalidChars`/`invalidCharacterRegex` in `app/src/lib/sanitize-ref-name.ts` to reject ref values beginning with `-`.
- Audit other call sites that interpolate branch/ref/remote names into git argv (e.g. `getMergedBranches`, `fetchRefspec`, `createBranch`) for the same "untrusted value before `--`" ordering issue.

## Proof of Concept
1. Host a link (e.g. on a webpage or in a README rendered by a service that allows `x-github-client://` links) of the form:
   `x-github-client://openRepo/https://github.com/<owner>/<repo>?branch=--orphan%3Dpwned`
2. `parseAppURL` accepts this because `--orphan=pwned` contains none of the characters rejected by `invalidCharacterRegex`. [6](#0-5) 
3. User clicks the link; Desktop calls `openBranchNameFromUrl(url, '--orphan=pwned')`. [3](#0-2) 
4. If the resulting branch-name value reaches `getBranchCheckoutArgs`, the constructed argv `['checkout', branch.name, '--']` causes git to interpret `--orphan=pwned` as a flag instead of a ref, since `--` appears after it in the argument list. [4](#0-3) 

**Note on verification limits:** I could not locate the source of `checkoutLocalBranch` in the indexed codebase (only its call sites), so step 3→4 traversal is inferred from naming and the shared `checkout.ts` module used elsewhere for branch checkout, not directly confirmed. A Devin session with full repository access should verify `checkoutLocalBranch`'s implementation to confirm the exact argv path before treating this as fully proven.

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

**File:** app/src/lib/git/checkout.ts (L24-35)
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
```

**File:** app/test/unit/git/checkout-test.ts (L21-42)
```typescript
describe('git/checkout', () => {
  it('throws when invalid characters are used for branch name', async t => {
    const repository = await setupEmptyRepository(t)

    const branch: Branch = {
      name: '..',
      nameWithoutRemote: '..',
      upstream: null,
      upstreamWithoutRemote: null,
      type: BranchType.Local,
      tip: { sha: '' },
      remoteName: null,
      upstreamRemoteName: null,
      isDesktopForkRemoteBranch: false,
      ref: '',
    }

    await assert.rejects(
      checkoutBranch(repository, branch, null),
      /fatal: invalid reference: ..\n/
    )
  })
```
