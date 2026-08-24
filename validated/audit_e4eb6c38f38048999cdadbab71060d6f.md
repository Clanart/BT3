## Title
Deep-link branch name flag/argument injection into `git checkout` via `testForInvalidChars` bypass — ([File: app/src/lib/parse-app-url.ts])

### Summary
The `x-github-client://openRepo/...?branch=...` deep-link handler validates the attacker-supplied `branch` query parameter with `testForInvalidChars` before it is fed straight into `git checkout` arguments. That validator does not block a leading `-`, unlike the sibling function `sanitizedRefName`, which explicitly strips leading `-`/`+` characters for the exact same "ref name" concept. This inconsistency lets a crafted deep link smuggle a git option (e.g. `-`/`--`-prefixed string) as the first positional argument to `git checkout`, and the flow that consumes it (`openBranchNameFromUrl`) executes with **no user confirmation** whenever the target repository is already cloned and its remote URL matches.

### Finding Description
`parseAppURL` in [1](#0-0)  only rejects a `branch` value if it matches `invalidCharacterRegex`, which blocks control characters, `~^:?*[\|""<>`, `@{`, consecutive dots, leading/trailing dot, `.lock$`, trailing `/` — but **not a leading hyphen**: [2](#0-1) 

Contrast this with `sanitizedRefName`, which is used elsewhere to build new branch names and explicitly runs `.replace(/^[-\+]*/g, '')` to strip leading `-`/`+` — an acknowledgment elsewhere in the codebase that leading dashes in a ref name are unsafe. `testForInvalidChars`, the function actually used to gate untrusted deep-link input, has no equivalent protection.

The unsanitized `branch` value flows through `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl`: [3](#0-2) [4](#0-3) 

Critically, if the URL's `url` already matches an already-cloned local repository (`doesRepositoryMatchUrl`), `openOrCloneRepository`/`openBranchNameFromUrl` proceeds **without showing any Clone/confirmation popup** — it directly fetches and calls `this.checkoutLocalBranch(repository, branchName)`. This is the only guarded path (`open-repository-from-url` for a *new* repo goes through a `CloneRepository` popup requiring explicit user action), but for an *existing* repo the branch name is passed straight into git checkout machinery.

The downstream `checkoutBranch` git invocation builds its argument list by placing the branch name as the **first positional token**, with `--` only appended *after* it: [5](#0-4) 
Because `--` is placed after the branch name rather than before it, a branch string beginning with `-` is not guaranteed to be treated as a literal ref by git — it can be parsed as an option to `git checkout` instead of a `--` end-of-options marker preceding it, defeating the intended protection.

### Impact Explanation
This breaks the invariant that CLI/URL-sourced ref names are inert data passed to git, not option flags. An attacker who controls a deep link (e.g., embedded in a webpage, chat message, or malicious `openRepo` link) that a Desktop user clicks can inject a leading-dash "branch" value that reaches `git checkout` as a raw positional argument for a repository the victim already has cloned — with **no confirmation dialog**, since the "already cloned, url matches" path skips the CloneRepository popup entirely. Depending on which git checkout option is smuggled, this could corrupt the working tree/branch state unexpectedly (e.g. `-b`/`-B` forcing branch creation/reset, `--orphan`, `--force`) or otherwise cause unintended git behavior — satisfying "silent corruption of what the user commits/pushes" from an attacker-controlled deep link, matching the report's underlying class: **a security-relevant input (ref/loan identifier) is validated by an incomplete allow/deny check when a stricter, correctly-scoped sanitizer already exists elsewhere in the codebase.**

### Likelihood Explanation
Requires only that the victim click a single `x-github-client://openRepo/...` link while having a matching repository already cloned (a common state for active GitHub Desktop users working with GitHub-hosted repos). No local access, no malware, and no unnatural multi-step user action is needed beyond the normal single-click deep-link flow Desktop is designed to support.

### Recommendation
Use the same leading-dash stripping/rejection logic in `testForInvalidChars` (or equivalently reject any `branch` value beginning with `-`) before it is accepted from `parseAppURL`. Additionally, harden `getBranchCheckoutArgs`/`checkoutBranch` to place the `--` end-of-options separator *before* the branch name argument (defense in depth), not only after it, so that any untrusted ref string cannot be interpreted as a flag regardless of upstream validation gaps.

### Proof of Concept
1. Attacker crafts: `x-github-client://openRepo/https://github.com/victim-org/victim-repo?branch=--<injected-flag>`
2. Victim has `victim-org/victim-repo` already cloned in Desktop with matching origin URL.
3. Victim clicks the link (or it's opened via `open-url`/protocol-launcher in `app/src/main-process/main.ts`).
4. `parseAppURL` accepts the branch string because `testForInvalidChars` does not flag a leading `-`.
5. `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl` runs with **no popup**, because the repo already exists and its URL matches, then calls `checkoutLocalBranch`/`checkoutBranch`, passing the attacker string as the first positional git-checkout argument.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
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
