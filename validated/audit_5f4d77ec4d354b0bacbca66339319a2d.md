### Title
Deep-Link Branch Parameter Bypasses Ref-Name Validation and Reaches Unshielded Git Argument Vectors, Enabling Argument Injection During Checkout/Branch Creation - (File: `app/src/lib/parse-app-url.ts`, `app/src/lib/git/branch.ts`, `app/src/lib/git/checkout.ts`)

### Summary
The external report's broken invariant is: a value is validated by one component (EVM encoder) but consumed by a different component (Solana decoder) that enforces stricter/different constraints, and the irreversible action (burn) happens before the mismatch is caught. The Desktop analog is a validation/consumption mismatch between the `x-github-client://openRepo/...?branch=` deep-link handler and the git commands that ultimately consume the `branch` value: the deep-link parser accepts branch names that Desktop's own sanitizer would reject, and the git command builders for `branch` and `checkout` do not shield that value from being parsed as a CLI flag instead of a ref name.

### Finding Description
An attacker can craft an `x-github-client://openRepo/<url>?branch=<value>` deep link. `parseAppURL` only rejects the `branch` value using `testForInvalidChars`: [1](#0-0) 

`testForInvalidChars` reuses the same regex as `sanitizedRefName`, but only the sanitizer additionally strips a leading `-`/`+` via a second, separate `.replace(/^[-\+]*/g, '')` step that `testForInvalidChars` never applies: [2](#0-1) 

This means a `branch` value such as `--orphan`, `-f`, or `-D` passes `testForInvalidChars` (it contains no control characters, no `~^:?*[\|<>`, no `..`, doesn't end in `.lock`) even though the app's own UI-facing sanitizer (`RefNameTextBox`) would have stripped or flagged it as invalid.

This unsanitized value flows straight into `IOpenRepositoryFromURLAction.branch` and from there into `Dispatcher.openRepositoryFromUrl` → `openBranchNameFromUrl(url, branch)`, which passes the raw string to `checkoutLocalBranch`: [3](#0-2) 

Downstream, the raw name is used to build git command argument arrays with no `--` end-of-options guard, or with `--` placed too late to matter:

- `createBranch` builds `['branch', name, startPoint]` with no separator preceding `name`, so a `name` starting with `-` is parsed by git as an option rather than a ref name: [4](#0-3) 

- `getBranchCheckoutArgs` places `--` *after* `branch.name` (`[branch.name, ..., '--']`), which does not protect `branch.name` from being interpreted as a flag if it begins with `-`: [5](#0-4) 

This is structurally the same class of bug as the report: one layer's validation (`testForInvalidChars`) is weaker than the constraint the consuming layer (`git branch`/`git checkout` CLI argument parser) actually requires, and the mismatch is not caught before the operation executes.

### Impact Explanation
A crafted deep link can smuggle a `-`-prefixed string into a `git branch`/`git checkout` invocation as an unintended CLI option instead of a ref name. Depending on which flag is injected (e.g., `-f`/`--force`, `--orphan`, `-D`), this can silently force-checkout over uncommitted local changes, reset/overwrite an existing branch ref, or detach history — i.e., silent corruption of the state the user will next commit or push, without any confirmation dialog, since the app believes it is performing a normal "checkout branch from URL" action. This falls squarely within the "silent corruption of what the user commits or pushes" impact class, triggered purely by the user clicking an attacker-supplied link — no local access, malware, or leaked credentials required.

### Likelihood Explanation
The trigger requires only that the user click a `x-github-client://openRepo/...?branch=...` link (or an equivalent GitHub.com "Open in Desktop" link), which Desktop registers itself to handle. The validation gap (`testForInvalidChars` vs. `sanitizedRefName`) is a straightforward oversight — the two functions are supposed to enforce the same ref-name policy but diverge on the leading-dash case — making this a realistic, low-effort attacker primitive.

### Recommendation
1. Make `testForInvalidChars` (or the deep-link handler) reject/normalize leading `-`/`+` exactly like `sanitizedRefName`, so the two functions enforce an identical, single canonical ref-name policy.
2. Insert an explicit `--` end-of-options marker immediately before any user- or remote-derived ref name in every git argument array (`branch.ts` `createBranch`, `checkout.ts` `getBranchCheckoutArgs`, and any other spot building `git branch`/`git checkout`/`git tag` argument arrays), rather than relying on placement after the ref or omitting it.
3. As defense in depth, validate that any branch name reaching a git command is a syntactically valid ref per `git check-ref-format` semantics (already partially implemented) *and* does not begin with `-`.

### Proof of Concept
1. Register/observe that Desktop handles `x-github-client://` URLs (`app/src/main-process/main.ts` `handleAppURL`).
2. Send the victim a link: `x-github-client://openRepo/https://github.com/<owner>/<repo>?branch=-f`.
3. `parseAppURL` accepts it (`branch = '-f'` passes `testForInvalidChars`) and returns an `open-repository-from-url` action. [1](#0-0) 
4. `Dispatcher.dispatchURLAction` routes to `openRepositoryFromUrl` → `openBranchNameFromUrl(url, '-f')` → `checkoutLocalBranch(repository, '-f')`. [3](#0-2) 
5. If a branch resolution/creation path is exercised with this raw string, `createBranch`'s unshielded argument array (`['branch', '-f', startPoint]`) or `checkout.ts`'s late `--` placement causes git to interpret `-f` as a flag, producing an unintended git operation instead of a normal ref checkout. [4](#0-3) [5](#0-4) 

Note: I was not able to locate the exact body of `checkoutLocalBranch` in the indexed code (only its call sites), so I cannot confirm with certainty which specific git flag combination is reachable end-to-end or whether an intermediate existence check short-circuits some injected values. This should be verified by a Devin session with full repository access before treating the exploit chain as fully confirmed; the validation-mismatch and unshielded-argument-array primitives themselves, however, are directly evidenced in the cited files.

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
