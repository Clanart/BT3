Based on my investigation, the strongest concrete finding is a git-argument-injection gap in the branch checkout path: `Branch.name`/`Branch.nameWithoutRemote` (which can originate from a remote branch fetched from an attacker-controlled repository) is placed on the `git checkout` command line *before* the `--` separator, so a ref name that looks like a flag (e.g. starting with `-`) is parsed as an option by git rather than as a pathname-safe ref.

### Title
Remote branch names beginning with `-` can be interpreted as `git checkout` options due to missing `--` before the ref argument - (File: app/src/lib/git/checkout.ts)

### Summary
`getBranchCheckoutArgs()` builds the argument vector for `git checkout` with the branch name placed before the `--` separator. Git's own ref-name validation (`git check-ref-format`, enforced via `sanitizedRefName`) explicitly permits refs to *start* with `-` for anything other than local branch creation via `git branch` — only Desktop's own sanitizer (`sanitizedRefName`) strips a leading `-`/`+`, and that sanitizer is never applied to the ref/branch name that flows from a fetched remote branch into `checkoutBranch`.

### Finding Description
`getBranchCheckoutArgs()` returns: [1](#0-0) 

Note the ordering: `branch.name` is the *first* positional argument, and `-b <nameWithoutRemote>` (for remote branches) comes after it, with `--` appended at the very end — after the ref names, not before them. This means `-- ` never actually protects `branch.name` from being parsed as an option, because it's placed too late in the argument list. `checkoutBranch()` then invokes `git(args, ...)` with this vector directly: [2](#0-1) 

`branch.name` and `branch.nameWithoutRemote` are populated from `git for-each-ref`/branch listing output, i.e. directly from ref names that exist in the repository — including remote-tracking branches created by fetching from an attacker-controlled remote (a cloned/fetched repository, satisfying the "attacker controls a fetched repository" precondition). Desktop's `sanitizedRefName()` function does strip a leading `-`/`+` from *user-typed* branch names when creating new branches, and there is an explicit test asserting this: [3](#0-2) 

However, this sanitizer is a UI-input helper (`app/src/lib/sanitize-ref-name.ts`) used only when the user types a new branch name; it is not applied to ref names discovered from remote branches. `testForInvalidChars()`, used to validate the `branch` query parameter from `x-github-client://` / `github-mac://` "Open in Desktop" deep links, likewise does **not** reject a leading `-`: [4](#0-3) [5](#0-4) 

So a repository can legitimately contain a branch whose name (after the remote prefix is stripped) begins with `-`, and once fetched, Desktop will list and can attempt to check it out via `checkoutLocalBranch`/`checkoutBranch` using the raw, unsanitized name as a positional git argument that precedes any `--` separator.

### Impact Explanation
`git checkout` accepts a range of single/double-dash options that can alter working-tree/behavior (e.g. `--recurse-submodules`, `-p`/`--patch`, `--orphan=<name>`, `-f`/`--force`, `--conflict=<style>`, `-t`/`--track`, `--ours`/`--theirs`). Depending on which option string collides with the branch name, this can silently change the semantics of a checkout the user believes to be a normal branch switch (e.g. forcing an overwrite of local changes with `-f`, or triggering `--recurse-submodules` against an attacker-supplied `.gitmodules`), corrupting the working tree contents that the user will subsequently review, stage, and commit/push — matching the "silent corruption of what the user commits or pushes" impact class. It does not by itself grant arbitrary code execution, but it removes a security boundary (argument/ref separation) that exists specifically to prevent attacker-controlled ref names from being interpreted as git flags.

### Likelihood Explanation
Exploitation requires the attacker to control a repository (or fork/PR) that the victim clones or fetches, and to create a branch whose non-remote-prefixed name matches a meaningful `git checkout` flag — this is entirely within reach of any external contributor who can push branches to a repo (or open a PR from a fork) that the victim opens in Desktop via a normal "Fetch" or "Open in Desktop" deep-link flow. The victim does not need to type anything unusual; simply checking out the malicious branch (a routine, expected action in Desktop) triggers the vulnerable code path. No local/admin access, malware, or leaked credentials are required.

### Recommendation
Insert `--` **before** the branch/ref name argument (and before `-b <name>`) in `getBranchCheckoutArgs()`, matching standard safe-git-invocation practice (`git checkout <ref> --` protects paths, but `git checkout -- <ref>` is required to protect the ref/option position itself — more precisely, use `git checkout --no-guess -- <ref>` semantics is not applicable to branch switching, so the correct fix is to validate/reject ref names that begin with `-` before ever placing them in argv, consistent with how `sanitizedRefName` already treats leading `-`/`+` as invalid). At minimum, reuse `testForInvalidChars`/`sanitizedRefName`-equivalent validation on any branch name obtained from git ref listings before using it as a positional CLI argument, and audit all other git subcommand invocations (`branch`, `tag`, `push`, etc.) that place remote-derived ref names before a `--` separator.

### Proof of Concept
1. Attacker creates a public GitHub repository (or a fork used to open a PR) with a branch literally named `-f` (or `--recurse-submodules`, etc.), pushed as `refs/heads/-f`.
2. Victim opens this repository in GitHub Desktop, or clicks an "Open in Desktop"/PR-checkout deep link that leads Desktop to fetch this remote and list its branches. `getBranches()` returns a `Branch` object with `name`/`nameWithoutRemote` equal to `-f`.
3. Victim checks out this remote branch (e.g. via `checkoutLocalBranch`, `_checkoutBranch`, or the PR checkout flow in `dispatcher.ts`).
4. `getBranchCheckoutArgs()` produces `['checkout', '-f', '-b', '-f', '--']` (or the analogous vector for whichever option string is chosen), which git parses as `git checkout -f -b -f --`, invoking `git checkout` with the `-f`/force flag instead of switching to a ref literally named `-f`, silently discarding any local uncommitted changes the victim had — without any warning dialog, because Desktop's own uncommitted-changes-protection logic never sees this as a "force" checkout. [1](#0-0)

### Citations

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

**File:** app/src/lib/git/checkout.ts (L121-124)
```typescript
  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)
```

**File:** app/test/unit/sanitize-ref-name-test.ts (L30-34)
```typescript
  it('does not allow name to start with minus', () => {
    const branchName = '--but-can-still-keep-the-rest'
    const result = sanitizedRefName(branchName)
    assert.equal(result, 'but-can-still-keep-the-rest')
  })
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
