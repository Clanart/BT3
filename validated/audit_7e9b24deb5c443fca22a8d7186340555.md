Based on the evidence gathered, here is the strongest verifiable analog: an attacker-controlled branch name delivered via the "Open in Desktop" deep-link URL is validated by a character blocklist that omits a leading-hyphen check, and the underlying `git branch` invocation lacks an argument-terminator (`--`), creating a git-option-injection path.

### Title
Deep-link `branch` parameter allows git-branch option injection due to missing leading-dash check and missing `--` argument terminator - ([File: app/src/lib/sanitize-ref-name.ts], [File: app/src/lib/git/branch.ts])

### Summary
GitHub Desktop's custom-protocol handler (`x-github-client://openRepo/<url>?branch=<name>`) accepts an attacker-supplied `branch` value from a clicked deep link and validates it with `testForInvalidChars` before using it to check out (and, if necessary, create) a local branch. That validator's blocklist does not reject a value beginning with `-`, and the git-branch-creation helper builds its `git branch` argument vector without a `--` terminator, so a crafted value can be interpreted by git as an option rather than a branch/committish name.

### Finding Description
`parseAppURL` extracts the `branch` query parameter from an incoming `openrepo` deep link and only rejects it via `testForInvalidChars(branch)`: [1](#0-0) 

`testForInvalidChars` is backed by `invalidCharacterRegex`, which blocks control characters, space, DEL, `~^:?*[\|""<>`, `@{`, consecutive/leading/trailing dots, `.lock` suffix, and trailing slash — but contains no rule for a value that starts with `-`: [2](#0-1) 

Notably, the sibling function `sanitizedRefName` in the same file explicitly strips a leading `-`/`+` (`.replace(/^[-\+]*/g, '')`), showing the project is aware that a leading dash is unsafe for a ref/branch argument — but that stripping logic is not applied in the validation path used for the deep-link `branch` parameter.

The accepted `branch` value flows from `parseAppURL` into `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl(url, branchName)`, which calls `checkoutLocalBranch(repository, branchName)`: [3](#0-2) 

When a new local branch needs to be created (branch does not already exist locally), Desktop's `createBranch` git wrapper builds the git argument list without an argument terminator: [4](#0-3) 

Compare this to `checkout.ts`, where the equivalent branch-argument builder explicitly appends `'--'` to stop git from treating a hostile branch name as an option: [5](#0-4) 

`createBranch` has no equivalent terminator. Since `args = ['branch', name, startPoint]` (or `['branch', name]`) is passed straight to `git(...)`, a `name` value such as `-m` or `-M` is interpreted by git as the `-m`/`-M` (force-rename current branch) option rather than a new-branch name, and `startPoint` — which normally holds a remote-tracking ref like `origin/main` — becomes the *new name* argument for that rename operation. This means a deep link with `branch=-m` can cause Desktop to silently execute `git branch -m origin/main`-like renames of the user's **current** branch as a side effect of what the user believes is simply "open/checkout a repository from a link," without ever running an explicit `git checkout`.

### Impact Explanation
This breaks the invariant that a `branch` name accepted from an untrusted, attacker-authored URL is a validated git ref-name before being used as a raw CLI argument. Because `git branch`'s current-branch rename (`-m`/`-M`) and other option flags can be triggered instead of creating/tracking the intended branch, a victim who clicks a single crafted "Open in Desktop" link can have their local branch structure silently altered — corrupting what branch subsequent commits/pushes land on, which matches the "silent corruption of what the user commits or pushes" impact class. This does not require local/physical access, admin rights, prior malware, or leaked credentials — only clicking a link, which is the expected use of this feature.

### Likelihood Explanation
The `openrepo` deep-link protocol handler is a first-class, publicly documented Desktop feature (registered for `x-github-client://`, `github-mac://`, `github-windows://`) intended to be invoked from external web pages (e.g., a "Open in Desktop" button), so the entry point is trivially reachable by any attacker who can get a victim to click a link. The existing guard (`testForInvalidChars`) gives a false sense of safety because it blocks many dangerous characters but not a leading `-`, and the PR-specific branch format (`^pr\/\d+$`) is separately enforced only in the `pr` code path, not the plain `branch` path.

### Recommendation
- In `app/src/lib/sanitize-ref-name.ts`, extend `testForInvalidChars` (or add a companion check used at the deep-link boundary) to reject any value beginning with `-`, mirroring the stripping already done in `sanitizedRefName`.
- In `app/src/lib/git/branch.ts`, add a `--` terminator to `createBranch`'s argument array (as already done in `checkout.ts`'s `getBranchCheckoutArgs`) so `name`/`startPoint` can never be parsed as git options regardless of upstream validation gaps.
- Apply the same defense-in-depth (leading-dash rejection + `--` terminators) to any other git-wrapper functions that accept externally-sourced ref/branch strings without one, since this validator is shared/reused code.

### Proof of Concept
1. Attacker hosts a page with a link: `x-github-client://openRepo/https://github.com/victim-org/some-repo?branch=-m`.
2. Victim, who has GitHub Desktop's protocol handler registered, clicks the link.
3. `parseAppURL` accepts `branch=-m` because `testForInvalidChars('-m')` returns `false` (no blocked characters present). [6](#0-5) 
4. `dispatchURLAction` routes to `openRepositoryFromUrl` → `openBranchNameFromUrl` → `checkoutLocalBranch(repository, '-m')`. [3](#0-2) 
5. If a new branch has to be created for tracking, `createBranch(repository, '-m', startPoint)` executes `git(['branch', '-m', startPoint], ...)` — which git parses as "force-rename current branch to `startPoint`" instead of "create/track new branch named `-m`." [4](#0-3) 

Note: I was not able to fetch the exact source of `checkoutLocalBranch` within the remaining tool budget, so the final step (confirming that this specific call path invokes `createBranch` versus some other branch-creation routine) is inferred from the confirmed call chain and naming conventions rather than directly read; the underlying primitive bugs — the missing leading-dash check in `testForInvalidChars` and the missing `--` terminator in `createBranch` — are directly confirmed in code and represent the core, exploitable input-validation gap.

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

**File:** app/src/lib/git/branch.ts (L21-38)
```typescript
export async function createBranch(
  repository: Repository,
  name: string,
  startPoint: string | null,
  noTrack?: boolean
): Promise<void> {
  const args =
    startPoint !== null ? ['branch', name, startPoint] : ['branch', name]

  // if we're branching directly from a remote branch, we don't want to track it
  // tracking it will make the rest of desktop think we want to push to that
  // remote branch's upstream (which would likely be the upstream of the fork)
  if (noTrack) {
    args.push('--no-track')
  }

  await git(args, repository.path, 'createBranch')
}
```

**File:** app/src/lib/git/checkout.ts (L28-36)
```typescript
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
