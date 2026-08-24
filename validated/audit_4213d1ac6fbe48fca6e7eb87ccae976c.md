### Title
Git branch-name argument injection via `x-github-client://openRepo` deep link - (File: `app/src/lib/parse-app-url.ts`)

### Summary
The `x-github-client://openRepo/...` deep-link handler accepts a `branch` query parameter and, in the general (non-PR) case, validates it only with a "does it contain forbidden ref characters" test. That test does not reject a value beginning with `-` or `--`. The resulting string is then handed, effectively unmodified, to the branch-checkout code path triggered automatically as soon as the user clicks the link. This is directly analogous to the SCP report's root cause: an untrusted external message ("value"/branch name) is accepted with a validity check that is much weaker than what downstream code actually assumes, allowing an attacker-controlled input to reach a sensitive operation unchecked.

### Finding Description
`parseAppURL` handles the `openrepo` action and extracts `branch` from the query string: [1](#0-0) 

For the `pr` flow, the branch value is forced to match the strict pattern `^pr\/\d+$`, but for the general/no-PR flow the only check applied is `testForInvalidChars(branch)`: [2](#0-1) 

`testForInvalidChars` re-uses the ref-name-illegal-character regex, but that regex targets control characters, spaces, and Git ref metacharacters (`~^:?*[\|"<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix) — it does **not** flag a value that starts with `-` or `--`: [3](#0-2) 

Note that the companion function `sanitizedRefName` in the same file does strip a leading run of `-`/`+` characters, but that stripping is not applied in `parse-app-url.ts` — only the non-mutating `testForInvalidChars` test is used. So a value like `--upload-pack=/tmp/evil.sh` passes validation untouched (no control chars, no spaces, no `~^:?*[\|"<>`, no `..`, no leading/trailing `.`).

The parsed `branch` value flows straight into `dispatcher.ts`'s `openRepositoryFromUrl`, which — when no `pr` is present but a `branch` is — calls `openBranchNameFromUrl(url, branch)`, which fetches the remote and then calls `this.checkoutLocalBranch(repository, branchName)` with the attacker-supplied string as-is: [4](#0-3) [5](#0-4) 

Because this handler is reachable purely by the user clicking a crafted `x-github-client://openRepo/<url>?branch=<payload>` link (registered as the OS protocol handler in `app/src/main-process/main.ts`'s `handleAppURL`), the attacker fully controls the string that ends up as a "branch name" argument passed into the checkout machinery, with only a partial character blacklist standing in the way.

### Impact Explanation
If the underlying `checkout`/`git` invocation (in `app/src/lib/git/checkout.ts` / `app-store.ts`'s `_checkoutBranch`) does not insert a `--` separator before the ref argument, a value such as `--upload-pack=...` or other `git checkout`/`git fetch`-adjacent flags could be interpreted as a command-line option rather than a literal ref name, potentially altering git's behavior (e.g., invoking an attacker-controlled helper program, or corrupting the checked-out state). At minimum, this represents an input-validation gap that lets attacker data reach a git command construction site with far weaker checks than the strict pattern used in the adjacent PR-branch code path, which is inconsistent and suggests the general branch path was overlooked.

I was not able to inspect `app/src/lib/git/checkout.ts`'s exact argument array construction (whether it prepends `--`) within the available tool budget, so I cannot confirm with certainty that this reaches actual argument-injection/code-execution as opposed to merely a failed/odd checkout. This is the key remaining uncertainty.

### Likelihood Explanation
The attack requires only that the victim click an `x-github-client://openRepo/...` link (or open one delivered via email, chat, or a malicious webpage) — no local access, credentials, or prior compromise needed, matching the report's "attacker controls a link the user clicks" primitive. The asymmetry between the strict `^pr\/\d+$` validation on the PR-branch path and the weak `testForInvalidChars`-only validation on the general branch path indicates this was not a deliberate, hardened design decision, increasing confidence that it's an oversight rather than an accepted risk.

### Recommendation
- In `app/src/lib/parse-app-url.ts`, reject (or use `sanitizedRefName` to normalize) any `branch` value that begins with `-`, in addition to the existing `testForInvalidChars` check, before returning it as part of `IOpenRepositoryFromURLAction`.
- In `checkoutLocalBranch`/the underlying `git checkout` invocation, always pass `--` before the ref name argument so that no user- or attacker-supplied ref string can be interpreted as a flag, regardless of upstream validation.
- Add a mesh-style/fuzz test analogous to the recommended SCP malicious-node tests: feed `parseAppURL`/`openBranchNameFromUrl` a corpus of branch strings starting with `-`/`--` and assert none reach the git argument list unescaped.

### Proof of Concept
1. Register/observe that GitHub Desktop is the default handler for `x-github-client://`.
2. Send or have the victim click:
   `x-github-client://openRepo/https://github.com/<owner>/<repo>?branch=--upload-pack=/tmp/payload.sh`
3. `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'https://github.com/<owner>/<repo>', branch: '--upload-pack=/tmp/payload.sh', pr: null, filepath: null }` because `testForInvalidChars` does not flag the leading `--`. [6](#0-5) 
4. `dispatcher.ts` clones/opens the repo, fetches, and calls `checkoutLocalBranch(repository, '--upload-pack=/tmp/payload.sh')`. [7](#0-6) 
5. Whether this results in flag injection depends on the unverified argument construction in the downstream `git checkout` call, which should be audited/hardened per the recommendation above regardless.

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
