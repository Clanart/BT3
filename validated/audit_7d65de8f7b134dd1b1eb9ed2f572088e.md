## Title
No `--` end-of-options guard in `renameBranch` allows argument injection via a crafted branch name - (File: app/src/lib/git/branch.ts)

### Summary
`renameBranch` builds the argument list `['branch', force ? '-M' : '-m', branch.nameWithoutRemote, newName]` and passes it directly to `git()` without a `--` separator before the operand positions. [1](#0-0) 

### Finding Description
`branch.nameWithoutRemote` originates from the current `Branch` object, which is derived from ref data read out of the repository (e.g. via `for-each-ref`/`getBranches`). While GitHub Desktop's UI-driven branch creation sanitizes new names to strip leading `-`/`+` characters via `sanitizedRefName` [2](#0-1) , that sanitizer is applied at branch-*creation* time in the UI, not universally when *reading* refs back out of a repository's on-disk ref store. Git's own porcelain (`git branch <name>`) refuses to create a ref beginning with `-`, but a ref name beginning with `-` can still exist in a repository's `packed-refs`/loose refs if placed there other than through normal `git branch` (e.g., a cloned/fetched malicious repository could ship a crafted `packed-refs` file). If such a ref is read back into a `Branch` object and reaches `renameBranch`, the value is inserted as the third positional element in the `git branch -m <old> <new>` command with no preceding `--`, so a value like `--foo` would be interpreted by git as an option rather than as the old branch name.

Comparatively, other operations in this same file already guard equivalent operand positions using `--`, e.g. `checkoutBranch`'s argument builder appends a trailing `--` after operand list [3](#0-2) , and `checkoutPaths`/`checkoutConflictedFile` also use `--` before path operands [4](#0-3) . `renameBranch`, `deleteLocalBranch`, and `createBranch` in `branch.ts` do not follow this pattern. [5](#0-4) [6](#0-5) 

The `newName` parameter, by contrast, is user-typed through `RenameBranch` dialog's `RefNameTextBox`, prefilled with `props.branch.name` but editable by the user before submission [7](#0-6) ; I was not able to fully confirm within the available tool calls whether `RefNameTextBox` itself calls `sanitizedRefName` to strip a leading `-` from user input before `onValueChange` fires (I could not open `ref-name-text-box.tsx` before running out of iterations), so I cannot conclusively state whether `newName` is sanitized before reaching `renameBranch`.

### Impact Explanation
If an attacker can get a leading-dash ref name into a victim's repository ref store (e.g., via a crafted `packed-refs` file in a cloned/fetched repository) and the victim triggers a rename on that branch, `branch.nameWithoutRemote` would be passed as an operand that git could interpret as a flag to `git branch -m`, potentially altering command behavior. However, `git branch -m` accepts very few options and most injectable flags would not lead to code execution or file write outside the repo directly — the practical severity of an injected flag on `git branch -m` specifically is limited compared to sinks like `git checkout`/`git clone`/`git config`, which is a mitigating factor against a "Critical" rating for this specific function.

### Likelihood Explanation
Exploitability depends on two unconfirmed preconditions I could not fully validate given available tools: (1) whether Desktop ever reads/surfaces ref names from a repository's ref store without first passing them through `sanitizedRefName` or an equivalent check-ref-format validation, and (2) whether `RefNameTextBox` sanitizes `newName` before it reaches the dispatcher. Given the existence of `sanitizedRefName`/`testForInvalidChars` used elsewhere in the codebase specifically to strip leading `-`/`+`, and the retry logic in `renameBranch` that re-derives names purely from git's own branch listing (`getBranchNames`) [8](#0-7) , it's plausible that upstream validation already constrains these values in normal UI flows, making end-to-end exploitation via `renameBranch` alone uncertain without further investigation of `ref-name-text-box.tsx` and the ref-reading code path.

### Recommendation
Add a `--` end-of-options guard to the `renameBranch` git invocation (and similarly `deleteLocalBranch`, `createBranch`) consistent with the pattern already used in `checkout.ts`, e.g. `['branch', force ? '-M' : '-m', '--', branch.nameWithoutRemote, newName]`, and confirm `RefNameTextBox` enforces `sanitizedRefName`/rejects leading `-`/`+` for all ref-name text inputs.

### Proof of Concept
Not fully constructible from the available code/context — verifying an actual injectable path requires confirming (a) that `RefNameTextBox` does not sanitize `newName`, and (b) that a `Branch` object with a leading-dash `nameWithoutRemote` can be surfaced by Desktop from a crafted repository's ref data. I was unable to complete this verification within the available tool budget.

### Citations

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

**File:** app/src/lib/git/branch.ts (L54-59)
```typescript
  try {
    await git(
      ['branch', force ? '-M' : '-m', branch.nameWithoutRemote, newName],
      repository.path,
      'renameBranch'
    )
```

**File:** app/src/lib/git/branch.ts (L79-91)
```typescript
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
```

**File:** app/src/lib/git/branch.ts (L101-107)
```typescript
export async function deleteLocalBranch(
  repository: Repository,
  branchName: string
): Promise<true> {
  await git(['branch', '-D', branchName], repository.path, 'deleteLocalBranch')
  return true
}
```

**File:** app/src/lib/sanitize-ref-name.ts (L8-11)
```typescript
/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
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

**File:** app/src/lib/git/checkout.ts (L214-234)
```typescript
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
```

**File:** app/src/ui/rename-branch/rename-branch-dialog.tsx (L40-52)
```typescript
  public constructor(props: IRenameBranchProps) {
    super(props)

    this.state = { newName: props.branch.name, currentError: null }
  }

  public componentDidMount() {
    // Validate the pre-filled branch name on dialog open so existing
    // rule violations are shown immediately.
    if (this.state.newName !== '') {
      this.checkBranchRules(this.state.newName)
    }
  }
```
