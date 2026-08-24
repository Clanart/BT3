# Title
Git argument/option injection via unsanitized deep-link `branch` parameter reaching `git checkout` before the pathspec separator - (File: `app/src/lib/git/checkout.ts`, `app/src/lib/parse-app-url.ts`, `app/src/lib/sanitize-ref-name.ts`)

### Summary
Similar to the Solidity report's "unrestricted value causing a downstream operation to fail/misbehave" pattern, GitHub Desktop's deep-link handler accepts an attacker-controlled `branch` value from a clicked `x-github-client://openrepo/...&branch=...` URL and validates it with an incomplete character blocklist, `testForInvalidChars`, before feeding it straight into `git checkout` as a positional argument that is **not** protected by a `--` pathspec separator placed before it. The invariant that should hold — "a branch name must never be interpretable as a command-line flag" — is not enforced.

### Finding Description
`parseAppURL` accepts a `branch` query parameter from an app-URL/deep link and only rejects it if it matches `invalidCharacterRegex`: [1](#0-0) 

That regex only blocks control characters, space, DEL, and the set `~^:?*[\|""<>`, plus `@{`, consecutive/leading/trailing dots, `.lock` suffix, and a trailing slash: [2](#0-1) 

Critically, a leading `-` (or `--`) is **not** in the blocked set, so a branch value such as `--orphan=x` or any other dash-prefixed string passes `testForInvalidChars` unchanged and is returned as a valid `open-repository-from-url` action's `branch` field.

This value flows through `Dispatcher.openRepositoryFromUrl` → `openBranchNameFromUrl` → `checkoutLocalBranch`: [3](#0-2) 

Eventually the branch name reaches `git/checkout.ts`, where the checkout arguments are built as `[branch.name, ...('-b' variant), '--']` — the pathspec-terminator `--` is appended **after** the branch name rather than before it: [4](#0-3) [5](#0-4) 

Because the branch string is placed before `--`, git's argument parser will interpret a value starting with `-`/`--` as an option to `git checkout` rather than as a ref/pathspec. Existing internal call sites are normally safe because `branch.name` comes from `git branch --list` output (git itself never produces refs starting with `-`), but the deep-link path constructs the "branch" identity directly from unauthenticated, attacker-supplied text without ever validating that it doesn't begin with `-`.

### Impact Explanation
This breaks the trust boundary described in the Valid Impact section: an attacker who controls a link/deep link the user clicks can smuggle a git flag into a `git checkout` invocation executed in the user's real, local clone. Depending on the flag accepted by the installed Git version (e.g., orphan-branch creation, worktree operations, or other checkout options), this can silently corrupt the state of what the user has checked out/committed, or cause the checkout command to behave in unexpected/destructive ways — matching the reports's "resulting in a failure due to unvalidated value" pattern, but with a security-relevant blast radius (corruption of git state driven entirely by a clicked link, no local access or malware required).

### Likelihood Explanation
The `x-github-client://openrepo` scheme is registered as a default OS protocol handler and is explicitly designed to be triggered by a link (e.g., from a compromised or malicious web page/email), matching the "link/deep link the user clicks" attacker primitive. No PR/`pr=` gating is required to reach the vulnerable `branch` field — only the plain "open repo + branch" deep-link path is needed, and the character blocklist does not consider leading dashes at all, so exploitation requires no bypass tricks beyond choosing a dash-prefixed value.

### Recommendation
- In `testForInvalidChars`/`sanitizedRefName` (`app/src/lib/sanitize-ref-name.ts`), reject or strip ref names that start with `-` (git already disallows this per `git check-ref-format --allow-onelevel`, but Desktop's custom regex does not).
- In `app/src/lib/git/checkout.ts`, always place the `--` pathspec separator immediately before the branch/ref argument (not after), so a hostile string can never be parsed as an option regardless of upstream validation.
- Treat any branch value obtained from a deep link/URL as untrusted input and validate it against `git check-ref-format` semantics (including the leading-dash rule) before it is ever placed in a git argument vector.

### Proof of Concept
1. Attacker crafts a link: `x-github-client://openrepo/https://github.com/victim/some-public-repo?branch=--orphan%3Dhack`
2. Victim (with the repo already cloned in Desktop or clonable) clicks the link.
3. `parseAppURL` accepts `branch = "--orphan=hack"` because none of its characters are in `invalidCharacterRegex`.
4. `openBranchNameFromUrl` → `checkoutLocalBranch` eventually invoke `checkoutBranch`, which runs `git checkout --progress --orphan=hack --` inside the user's real repository, causing git to interpret `--orphan=hack` as an option instead of a ref name, deviating from the expected "checkout existing/new branch" behavior.

Note: I could not fully trace the internal implementation of `Dispatcher.checkoutLocalBranch` (its exact body was not returned by the index), so I cannot confirm with certainty whether it performs any additional sanitization before constructing the `Branch` object passed into `checkoutBranch`. This is the main open uncertainty; verifying `app/src/ui/dispatcher/dispatcher.ts`'s `checkoutLocalBranch` method and `app/src/lib/stores/app-store.ts`'s `_checkoutBranch`/`checkoutImplementation` chain in full would be needed to conclusively confirm no additional guard exists between the deep-link `branch` string and the `git checkout` argument vector.

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

**File:** app/src/lib/git/checkout.ts (L102-124)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)
```
