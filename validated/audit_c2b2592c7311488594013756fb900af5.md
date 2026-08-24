## Analysis

The Perennial report's core defect is a **missing invariant check at a trust boundary**: attacker-influenced input (`pendingEpochs`) is accepted and used to drive state transitions without validating that it can't desynchronize two dependent pieces of state. The closest structurally-supported analog in GitHub Desktop is a **missing character-class check at a trust boundary for git ref names**, where attacker-controlled data (a branch name coming from a remote repository, surfaced through a deep link) is passed into a `git checkout` invocation in a position where a leading `-` is parsed as an option rather than a ref, i.e. **argument injection**.

### Title
Branch-name argument injection into `git checkout` via unsanitized ref names originating from a remote/deep link - (`app/src/lib/git/checkout.ts`)

### Summary
`checkoutBranch()` builds its `git checkout` argument list with the branch name placed **before** the `--` end-of-options marker: `['checkout', branch.name, '--']`. [1](#0-0) 
If `branch.name` (or the value passed to `-b`) begins with `-`, git will interpret it as an option to `checkout` instead of a positional ref, because it precedes the `--` separator that would otherwise stop option parsing.

Branch names reaching this code path can originate from an attacker-controlled remote/deep link: `parseAppURL()` accepts a `branch` query parameter from an `x-github-client://openrepo/...` URL and only rejects it via `testForInvalidChars()`. [2](#0-1) 
That validator is driven by `invalidCharacterRegex`, which blocks control characters, `~^:?*[\|"<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix, and trailing `/` — but it **does not reject a leading hyphen**. [3](#0-2) 

### Finding Description
The dispatcher's `openBranchNameFromUrl()` clones/opens the attacker-specified repository, fetches it, and then calls `checkoutLocalBranch()` with the (weakly validated) branch name. [4](#0-3) 
`checkoutLocalBranch()` looks up a `Branch` object whose `nameWithoutRemote` equals the attacker-supplied string among branches enumerated from the repository's real refs (via `for-each-ref`), then calls `checkoutBranch(repository, localBranch)`. [5](#0-4) 
Because this repository can be one the attacker fully controls (a URL supplied in the deep link, or the response served by a malicious/compromised git remote or MITM proxy during clone/fetch), the attacker can advertise a ref whose name starts with `-` (e.g. `refs/heads/--upload-pack=...` or a remote branch whose suffix after `origin/` is `--orphan=pwned`). Git's own `check-ref-format` does not forbid a leading hyphen in ref names, which is a well-known class of client-side argument-injection bugs affecting git porcelain wrappers that don't defensively guard against it (this is exactly why many git-consuming tools explicitly special-case leading `-`/`--`, disambiguating refs from options).

Desktop's own ref sanitizer (`sanitizedRefName`/`testForInvalidChars`) does not perform that specific check, and `getBranchCheckoutArgs()` places the ref-derived string in option-parseable position ahead of `--`: [6](#0-5) 
This is the "corrupted value": the branch/ref string that Desktop trusts as a plain positional argument is not guaranteed to be free of leading dashes, so it can be reinterpreted by git as a flag, silently redirecting what `git checkout` actually does (e.g. `--orphan=<name>` creates and switches to a new unrelated root branch instead of checking out the intended one — a form of "silent corruption of what the user commits/pushes", since subsequent commits would land on an attacker-chosen unrelated branch).

Existing guards do not stop this path because:
- `testForInvalidChars()` filters a specific set of characters but not leading `-`.
- `getBranchCheckoutArgs()` never inserts `--` **before** the ref argument, only after it, so option parsing is not disabled for the ref token itself.
- `checkoutLocalBranch()`'s "must already exist among enumerated branches" check is not a security boundary — the attacker controls which refs exist in the repository being cloned/fetched, so they can seed the exact malicious ref name the deep link references.

### Impact Explanation
If exploitable, clicking a `x-github-client://openrepo/<attacker-url>?branch=<crafted>` link (or a `--cli-clone`/`--cli-branch` command line launch, which follows the same code path) could cause `git checkout` to execute with an attacker-chosen option instead of switching to the intended branch. Depending on which git option resolves, this ranges from denial-of-service (checkout failure) to state corruption (checking out into an unrelated orphan branch, silently changing where subsequent commits/pushes land) without any explicit user confirmation of the actual git command executed.

### Likelihood Explanation
Exploitability is **not fully confirmed** from static review alone: whether git's client-side ref-writing (`update-ref`/`fetch`) will actually accept and materialize a local or remote-tracking ref whose final component begins with `-` depends on dugite/git version behavior that could not be verified from the indexed code. If git's own `check-ref-format` rejects such refs during fetch/clone (many git versions do reject ref components starting with `-` at the plumbing layer), this specific chain is not reachable, even though the input-validation gap (`testForInvalidChars` not blocking leading `-`) is real and independently worth hardening as defense in depth.

### Recommendation
- Explicitly reject branch/ref names starting with `-` in `testForInvalidChars()`/`sanitizedRefName()` (`app/src/lib/sanitize-ref-name.ts`), matching the mitigation other git front-ends apply for this exact bug class.
- In `getBranchCheckoutArgs()` (`app/src/lib/git/checkout.ts`), always place `--` immediately before the ref argument (not only trailing), so option parsing is disabled for the ref token regardless of its content: `['checkout', '--', branch.name]` style ordering (with `-b <name>` still requiring the same treatment for `nameWithoutRemote`).
- Add a regression test asserting that a `Branch` whose name begins with `-` cannot influence the constructed git argv in an option-parseable position.

### Proof of Concept
Not independently executable from the indexed code alone (requires a live malicious git remote and confirmation of dugite/git ref-acceptance behavior), but the reachable code path is:
1. Attacker hosts a git remote whose default/advertised branch's ref name begins with `-` (e.g. `--orphan=pwned`) — deliverable via `openRepositoryFromUrl` when the user clicks a crafted deep link (`app/src/ui/dispatcher/dispatcher.ts:1940-1996`) or via `--cli-clone --cli-branch` (`app/src/main-process/main.ts:282-291`).
2. `parseAppURL()` passes the branch string through unchanged since `testForInvalidChars()` does not flag a leading `-` (`app/src/lib/parse-app-url.ts:98-125`, `app/src/lib/sanitize-ref-name.ts:1-16`).
3. `checkoutLocalBranch()` locates the matching `Branch` object and calls `checkoutBranch()` (`app/src/ui/dispatcher/dispatcher.ts:2188-2213`).
4. `getBranchCheckoutArgs()` places `branch.name` ahead of `--` in the constructed `git checkout` argv (`app/src/lib/git/checkout.ts:24-36`), so if the name begins with `-` it is parsed as an option instead of a ref.

### Citations

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2188-2213)
```typescript
  private async checkoutLocalBranch(repository: Repository, branch: string) {
    let shouldCheckoutBranch = true

    const state = this.repositoryStateManager.get(repository)
    const branches = state.branchesState.allBranches

    const { tip } = state.branchesState

    if (tip.kind === TipState.Valid) {
      shouldCheckoutBranch = tip.branch.nameWithoutRemote !== branch
    }

    const localBranch = branches.find(b => b.nameWithoutRemote === branch)

    // N.B: This looks weird, and it is. _checkoutBranch used
    // to behave this way (silently ignoring checkout) when given
    // a branch name string that does not correspond to a local branch
    // in the git store. When rewriting _checkoutBranch
    // to remove the support for string branch names the behavior
    // was moved up to this method to not alter the current behavior.
    //
    // https://youtu.be/IjmtVKOAHPM
    if (shouldCheckoutBranch && localBranch !== undefined) {
      await this.checkoutBranch(repository, localBranch)
    }
  }
```
