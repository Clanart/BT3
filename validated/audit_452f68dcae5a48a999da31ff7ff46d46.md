This is a real, confirmed pattern in the codebase: `checkoutBranch` in `app/src/lib/git/checkout.ts` explicitly appends a trailing `'--'` argument via `getBranchCheckoutArgs` before the branch name is passed to `git checkout`, specifically to stop a branch name from being parsed as a flag: [1](#0-0) 

That defensive pattern is missing from `deleteLocalBranch`, which passes `branchName` directly as the last, unguarded argument to `git branch -D`: [2](#0-1) 

### Title
Missing `--` separator allows branch-name argument injection in `deleteLocalBranch` - (File: app/src/lib/git/branch.ts)

### Summary
`deleteLocalBranch(repository, branchName)` builds the argv `['branch', '-D', branchName]` and executes it via `git`, with no `--` separator to force `branchName` to be treated as a positional argument. If a local branch's ref name begins with a `-`, that string is passed straight to `git branch -D`, where `git`'s own argv parser interprets it as an option rather than a branch identifier.

### Finding Description
Git's `check_refname_format` rules (leading dot, `..`, control chars, `~^:?*[\`, trailing `.`/`.lock`, `@{`, etc.) do **not** forbid a ref/branch short-name from beginning with `-`; git only refuses to *create* such a name via a bare CLI invocation like `git branch -Dmaster` because the shell parser—not the ref-name validator—treats the leading dash as an option. Once such a name exists as a ref, however, (e.g., created with `git branch -- -Dmaster` locally, or synthesized by scaffolding/renaming/fetch-created refs), other git subcommands, including `git branch -D <name>`, will reparse it as flags if it's not isolated behind `--`. GitHub Desktop's own code already recognizes this exact class of problem: `checkoutBranch` appends a trailing `'--'` in `getBranchCheckoutArgs` [1](#0-0)  and `checkoutPaths`/`checkoutConflictedFile` also use `--` before untrusted path/branch data [3](#0-2) . `deleteLocalBranch`, `createBranch`, and `renameBranch` in `app/src/lib/git/branch.ts` do not follow this pattern [4](#0-3) .

The only mitigation for user-typed branch names is `sanitizedRefName`, which strips a leading `-`/`+`, but this is applied at the UI layer for names the user types when creating/renaming a branch [5](#0-4) ; it is **not** invoked on branch names that arrive from `Branch` objects obtained via `for-each-ref`/`getBranches` parsing of already-existing local or remote-tracking refs, which is what feeds `deleteLocalBranch` throughout `app-store.ts` and `branch-pruner.ts`.

### Impact Explanation
If a `-`-prefixed local ref name can be produced by a flow that does not go through `sanitizedRefName` (e.g., a locally created remote-tracking ref name derived from an attacker-controlled remote branch, or via the rename retry path in `renameBranch`), then `deleteLocalBranch` would hand that string to `git branch -D` unguarded, letting it be reinterpreted as one or more git-branch flags instead of a ref name. The concrete blast radius is limited to options recognized by `git branch` (e.g. `-f`, `-m`/`-M`, `-D`, `--edit-description`, etc.); it does not itself grant arbitrary code execution, file write outside the repo, or credential exfiltration, so as reported this is a scoped git-argv-corruption bug, not a full RCE/exfiltration primitive.

### Likelihood Explanation
Exploitability hinges entirely on whether an attacker can actually get a `-`-prefixed name into a `Branch` object that flows into `deleteLocalBranch` without passing through `sanitizedRefName`. The proof idea in the question (calling `deleteLocalBranch` directly with a synthetic `'-Dmaster'` string) demonstrates the argv-corruption mechanism but does not by itself demonstrate that GitHub Desktop's real UI/fetch/clone flows can *originate* such a branch name from attacker-controlled repository content — I could not confirm, in this codebase, an unsanitized path from a fetched/cloned remote ref straight into `deleteLocalBranch` that bypasses `sanitizedRefName` or an equivalent check (checkout-test.ts shows git itself already rejects malformed refs like `..` at the `checkoutBranch` layer, and I did not find a test/counterexample proving a leading-dash ref can survive from fetch through to deletion). Given the ask-only scope of this review, I can confirm the missing-`--` code pattern is real and inconsistent with `checkout.ts`'s own precedent, but cannot confirm end-to-end exploitability from a purely remote/repo-content-controlled attacker without deeper testing of the fetch/branch-listing pipeline.

### Recommendation
Add a `--` separator before the branch name in `deleteLocalBranch` (and audit `createBranch`/`renameBranch` similarly), mirroring the pattern already used in `checkout.ts`:
```ts
await git(['branch', '-D', '--', branchName], repository.path, 'deleteLocalBranch')
```
This costs nothing and closes the gap regardless of whether a concrete attacker-reachable path currently exists.

### Proof of Concept
Focused repo test (as suggested in the question):
```ts
const repository = await setupEmptyRepository(t)
// synthesize a branch name that git accepts only via `--`
await exec(['branch', '--', '-Dmaster'], repository.path)
await deleteLocalBranch(repository, '-Dmaster')
// without a `--` separator, `git branch -D -Dmaster` is parsed by git
// as combined short options rather than `-D <branchname>`
```
This demonstrates the argv-corruption mechanism at the `git` invocation layer; it does not by itself demonstrate a fully attacker-reachable trigger via clone/fetch, which remains unverified.

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

**File:** app/src/lib/git/checkout.ts (L209-235)
```typescript
/** Check out the paths at HEAD. */
export async function checkoutPaths(
  repository: Repository,
  paths: ReadonlyArray<string>
): Promise<void> {
  await git(
    ['checkout', 'HEAD', '--', ...paths],
    repository.path,
    'checkoutPaths'
  )
}

/**
 * Check out either stage #2 (ours) or #3 (theirs) for a conflicted
 * file.
 */
export async function checkoutConflictedFile(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  resolution: ManualConflictResolution
) {
  await git(
    ['checkout', `--${resolution}`, '--', file.path],
    repository.path,
    'checkoutConflictedFile'
  )
}
```

**File:** app/src/lib/git/branch.ts (L21-107)
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

export const getBranchNames = ({ path }: Repository): Promise<string[]> => {
  const parser = createForEachRefParser({ name: '%(refname:short)' })
  return git(['branch', ...parser.formatArgs], path, 'getBranchNames').then(x =>
    parser.parse(x.stdout).map(b => b.name)
  )
}

/** Rename the given branch to a new name. */
export async function renameBranch(
  repository: Repository,
  branch: Branch,
  newName: string,
  force?: boolean
): Promise<void> {
  try {
    await git(
      ['branch', force ? '-M' : '-m', branch.nameWithoutRemote, newName],
      repository.path,
      'renameBranch'
    )
  } catch (error) {
    // If we failed to rename and the branch name only differs by case, we
    // we'll try again with the -M flag to force the rename. See
    // https://github.com/desktop/desktop/issues/21320
    if (
      // Only retry if the caller hasn't explicitly asked us to force the rename
      force === undefined &&
      isGitError(error) &&
      error.result.gitError === DugiteError.BranchAlreadyExists
    ) {
      const stderr = coerceToString(error.result.stderr)
      const m = /fatal: a branch named '(.+?)' already exists/.exec(stderr)

      if (m && m[1].toLowerCase() === newName.toLowerCase()) {
        // At this point we're almost certain that we are dealing with a
        // case-only rename on a case insensitive filesystem, but we can't
        // be 100% sure, NTFS can be configured to be case sensitive and macOS
        // might have case sensitive file systems mounted so we have to list
        // all branches and check the names.
        return (
          getBranchNames(repository)
            // Throw the original error if we fail to get the branch names
            .catch(() => Promise.reject(error))
            .then(names =>
              // If we find the new name in the list of branches we can't
              // safely assume it's a case-only rename and have to
              // propagate the original error, otherwise try again with -M
              names.includes(newName)
                ? Promise.reject(error)
                : renameBranch(repository, branch, newName, true)
            )
        )
      }
    }
    throw error
  }
}

/**
 * Delete the branch locally.
 */
export async function deleteLocalBranch(
  repository: Repository,
  branchName: string
): Promise<true> {
  await git(['branch', '-D', branchName], repository.path, 'deleteLocalBranch')
  return true
}
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-11)
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
```
