## Title
Deep-link `branch` parameter reaches `git checkout` without stripping a leading `-`, enabling git option/argument injection - (File: `app/src/lib/parse-app-url.ts`, `app/src/lib/sanitize-ref-name.ts`, `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The external report's broken invariant is: *attacker-controlled input that is supposed to be "validated" before being used in a privileged operation is actually only partially validated, letting a syntactically-dangerous value slip through and be consumed by a downstream engine (EVM scale factor / git CLI) that interprets it in an unintended, higher-privilege way.* The GitHub Desktop analog is the `branch` query parameter of the `x-github-client://openRepo/...` protocol handler: it is checked with `testForInvalidChars`, but that check does not reject a leading `-`, so an attacker-controlled deep link can smuggle a git-option-looking string all the way into a `git checkout` invocation.

### Finding Description
`parseAppURL` extracts the `branch` query parameter directly from an attacker-supplied URL and only rejects it if `testForInvalidChars(branch)` is true: [1](#0-0) 

`testForInvalidChars` is backed by `sanitize-ref-name.ts`'s `invalidCharacterRegex`, which blocks control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, and leading/trailing dot — but it never blocks a leading `-` (or `--`): [2](#0-1) 

Note that the *sanitizing* function `sanitizedRefName` (used when the user types a branch name manually in the UI) strips a leading `-`/`+` via `.replace(/^[-\+]*/g, '')`, but the *validating* function `testForInvalidChars` used on the deep-link path does not enforce the same rule — an inconsistency between the two related utilities in the same file.

The unvalidated `branch` string then flows, unsanitized, through the URL-action pipeline:
- `handleAppURL` in `main.ts` parses the OS-delivered protocol URL and forwards the action to the renderer via `window.sendURLAction(action)`.
- `dispatchURLAction` routes `open-repository-from-url` actions to `openRepositoryFromUrl`: [3](#0-2) 

- `openRepositoryFromUrl` calls `openBranchNameFromUrl(url, branch)` when a branch is present, which in turn calls `this.checkoutLocalBranch(repository, branchName)`: [4](#0-3) 

`checkoutLocalBranch` ultimately drives the git checkout git-command wrapper with `branchName` as a positional ref argument. Because the value can begin with `-`/`--`, and nothing between the deep-link parser and the git-command call strips or quarantines a leading dash, the ref string can be interpreted by the `git` binary itself as an option rather than a ref name (classic CLI "argument injection"), the same broken-invariant shape as the original report: a value that is supposed to be treated purely as *data* (a ref name / a debt figure) is instead consumed by the executing engine (git / EVM interest accrual) as something that changes control flow (a flag / an accruing penalty) because the guard that should have neutralized it is incomplete.

### Impact Explanation
If the branch value reaches the underlying `git checkout` (or related plumbing) invocation as a raw positional argument without a preceding `--` separator, an attacker who gets a victim to click a single `x-github-client://openRepo/...?branch=...` link controls an argument to a `git` subprocess running with the user's full local privileges. Depending on which git subcommand ultimately consumes this string (checkout/switch/etc.), this class of bug has historically enabled data exfiltration, working-tree corruption, or arbitrary file writes via crafted git options (e.g., `--`-prefixed flags accepted by git plumbing commands). This satisfies the requested impact class: "a link or deep link the user clicks" resulting in code execution or file write/read outside expected bounds, or silent corruption of what the user commits/checks out.

### Likelihood Explanation
Likelihood is high for the *reachability* of attacker-controlled data: `parseAppURL` is directly exposed to any external protocol link (no user prompt validates the branch string's shape before use), and the existing tests confirm several strings do pass through untouched into `IOpenRepositoryFromURLAction.branch`: [5](#0-4) 

Whether it culminates in **the most severe** outcome (full option injection into git) depends on the exact git subcommand construction inside the checkout code path (`app/src/lib/git/checkout.ts`), which the index does not surface in full — that file was located but its `git(...)` call/args construction could not be fully retrieved in this session, so the presence/absence of a `--` separator before the ref argument is not independently confirmed here.

### Recommendation
- Apply the same leading `-`/`+` stripping (or outright rejection) in `testForInvalidChars`/`parseAppURL` that `sanitizedRefName` already performs, so deep-link branch names can never begin with a dash.
- Regardless of validation, always pass a literal `--` separator before any user/remote-controlled ref argument in every git invocation (`checkout`, `switch`, `branch`, etc.) so git can never interpret the value as an option, per git's own security guidance for wrapping tools.
- Add a unit test asserting `parseAppURL('...?branch=--upload-pack=x')` (and similar `-`-prefixed payloads) resolves to `unknown`.

### Proof of Concept
1. Attacker crafts and distributes a link: `x-github-client://openRepo/https://github.com/some/repo?branch=--upload-pack=touch%20/tmp/pwned`
2. Victim (with GitHub Desktop registered as the protocol handler) clicks the link in a browser or chat client.
3. OS invokes GitHub Desktop with the URL; `handleAppURL` → `parseAppURL` accepts the `branch` value because `testForInvalidChars` does not flag a leading `-`.
4. `dispatchURLAction` → `openRepositoryFromUrl` → `openBranchNameFromUrl` → `checkoutLocalBranch(repository, "--upload-pack=touch /tmp/pwned")` is invoked with the raw string, at which point the outcome hinges on how `app/src/lib/git/checkout.ts` builds its `dugite` argv (this final link was not fully verified with source in this session due to tool-call exhaustion, but the attacker-controlled value being passed unsanitized up to that call site is confirmed by the cited code).

Given the confirmed unsanitized-input pathway but unverified final `git` argv construction, this should be treated as a **strong candidate requiring a Devin session to confirm the exact `checkout.ts` invocation** before filing as a fully proven RCE-class bug.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2121)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break

```

**File:** app/test/unit/parse-app-url-test.ts (L42-51)
```typescript
    it('adds branch name if set', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/desktop/desktop?branch=cancel-2fa-flow'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/desktop/desktop')
      assert.equal(openRepo.branch, 'cancel-2fa-flow')
    })
```
