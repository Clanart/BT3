### Title
Unsanitized deep-link `branch` parameter flows into local branch checkout - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The external report's core defect is a normalization step that silently discards a security-relevant distinguishing property of untrusted input (`abs()` erasing sign), causing two different real values to be treated as identical by downstream logic. The closest verified analog in GitHub Desktop is `openBranchNameFromUrl`, which takes the `branch` parameter straight from a `x-github-client://` deep link — fully attacker-controlled — and passes it, unsanitized, into the branch-checkout pipeline, unlike every other branch-name code path in the app which goes through `sanitizedRefName`/`testForInvalidChars` before being used as a git ref.

### Finding Description
`openRepositoryFromUrl` dispatches to `openBranchNameFromUrl(url, branchName)` when a deep link contains a `branch` query parameter [1](#0-0) . That function immediately calls `this.checkoutLocalBranch(repository, branchName)` with the raw string taken from the URL [2](#0-1) .

Elsewhere in the codebase, any branch name that is derived from untrusted or user-typed input is passed through `sanitizedRefName`, which strips control characters, leading `-`/`+`, and other characters that git or the CLI could misinterpret [3](#0-2) . The dispatcher's deep-link handler does not apply this normalization (or any equivalent validation such as `testForInvalidChars`) to `branchName` before it reaches the checkout machinery. This mirrors the report's pattern: a value that must retain a security-relevant property (here, "does not begin with `-` / is a well-formed ref") is passed through unmodified/uninspected at exactly the point where an attacker controls it, while the sibling code paths that build branch names from user input do enforce that property.

### Impact Explanation
If the branch string eventually reaches a `git` invocation as a positional argument without a `--` separator (as is the case in the internal `checkoutBranch` argument builder, which appends `branch.name` before other flags) [4](#0-3) , a value such as `--upload-pack=...` or another dash-prefixed string supplied via the deep link could be interpreted as a git command-line option rather than a ref name. Because deep links are activated purely by the user clicking a link (the `x-github-client://openRepo?branch=...` flow), no local access, credentials, or prior compromise is required — this matches the "link or deep link the user clicks" attacker primitive in the Valid Impact criteria. Depending on which option is injected, outcomes range from unexpected file writes/ref manipulation to broader git argument-injection classes.

### Likelihood Explanation
Moderate. Exploitation requires only that the user click a crafted deep link while Desktop is installed and a matching repository can be opened/cloned — no other user interaction beyond the normal "Open in Desktop" flow. I was not able to fully trace the implementation of `Dispatcher.checkoutLocalBranch` within the remaining iteration budget to confirm whether it internally re-validates or sanitizes the branch string before invoking git; this is the main open verification item.

### Recommendation
Apply `sanitizedRefName` / `testForInvalidChars` (already implemented and used elsewhere in the codebase) to the `branchName` value in `openBranchNameFromUrl` before it is used to create or check out a branch, and ensure any git invocation that consumes it places a `--` separator before the ref argument, consistent with the pattern already used in `getBranchCheckoutArgs`.

### Proof of Concept
1. Attacker crafts a link: `x-github-client://openRepo/https://github.com/owner/repo?branch=--upload-pack=%2Fbin%2Ftouch`
2. Victim has GitHub Desktop installed and clicks the link.
3. `openRepositoryFromUrl` → `openBranchNameFromUrl` is invoked with the malicious `branch` value [5](#0-4) , which is passed to `checkoutLocalBranch` without sanitization [6](#0-5) .
4. Whether this results in argument injection depends on the unverified internals of `checkoutLocalBranch`; this should be confirmed with a Devin session that has full file access, since the index used here does not expose that method's body.

**Note on completeness:** I could not locate/inspect the body of `Dispatcher.checkoutLocalBranch` (only its two call sites appeared in search results) within the available tool budget, so the exact git invocation it triggers is unconfirmed. This limits certainty about whether the missing sanitization is actually exploitable end-to-end versus merely a defense-in-depth gap. A full-repo Devin session would be needed to verify this method's implementation and any git argument list it constructs.

### Citations

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

**File:** app/src/lib/sanitize-ref-name.ts (L1-18)
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
