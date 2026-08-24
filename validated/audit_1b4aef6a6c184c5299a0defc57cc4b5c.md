## Title
Argument injection via crafted remote branch name in `checkoutBranch` - (File: `app/src/lib/git/checkout.ts`)

### Summary
`getBranchCheckoutArgs` builds the argv for `git checkout` by placing `branch.name` as the *first* positional argument, and only appends the `--` path separator *after* it. Because `branch.name` for remote-tracking branches is taken verbatim from `%(refname:short)` returned by `git for-each-ref`/fetch (with no leading-dash stripping), a maliciously named remote branch such as `--upload-pack=...` reaches `git checkout` and is parsed as an option rather than a ref.

### Finding Description
`getBranchCheckoutArgs` is defined as: [1](#0-0) 

Note that `branch.name` is the first element of the returned array, and the `--` separator is appended at the *end*, after the optional `-b <nameWithoutRemote>`. This ordering does nothing to protect `branch.name`/`nameWithoutRemote` from being interpreted as options — `--` only tells git to stop parsing options for anything that comes *after* it, but `branch.name` is already before it.

`checkoutBranch` then concatenates this directly into the final argv passed to `git`: [2](#0-1) 

`branch.name` for remote branches comes from `getBranches`, which parses `%(refname:short)` directly out of `git for-each-ref` output with no validation or leading-dash stripping: [3](#0-2) 

Compare this to `app/src/lib/sanitize-ref-name.ts`, which explicitly strips leading `-`/`+` characters from ref names — but that sanitizer is only applied to *user-typed* new branch names (e.g. in `create-branch-dialog.tsx`), not to branch names that arrive from a fetched remote: [4](#0-3) 

Git's own ref-name validation rules (`git-check-ref-format`) do not forbid a ref/branch short name from beginning with a hyphen; the application's own sanitizer treats a leading `-`/`+` as something that must be actively stripped, which confirms git itself would otherwise accept such a name. A remote branch created by an attacker-controlled server/repo (e.g., `refs/remotes/origin/--upload-pack=...`) can therefore be fetched into the user's repository and surfaced as a `Branch` object whose `name` begins with `-`.

When the user (or any code path, e.g. clicking a branch in the branch list, or automated "restore last branch" logic) triggers `checkoutBranch` on that branch, the final argv becomes something like:
```
['checkout', <branch.name>, '-b', <nameWithoutRemote>, '--']
```
Since `<branch.name>` (e.g. `--upload-pack=touch${IFS}pwn`) is positioned immediately after `checkout` and before `--`, `git checkout` parses it as an option rather than a positional ref argument, letting the attacker inject arbitrary git-checkout flags (or, in variants of this flaw class, `--upload-pack=<cmd>`-style flags that cause an external command to be spawned) into the invocation.

### Impact Explanation
If a crafted flag can cause a helper program to be spawned or an unexpected local git configuration/behavior to be triggered (e.g., forcing `--upload-pack=<arbitrary command>`-style RCE primitives found historically in git-CLI argument-injection bugs, or forcing dangerous checkout modes), this leads to code execution or unintended repository state changes purely from fetching a repository, with no other action required beyond the app checking out a remote/tracking branch. This matches the "repo-write/code-exec impact" scope described.

### Likelihood Explanation
Likelihood is high given the required conditions: (1) attacker needs to control a remote (any public/malicious clone URL added by the victim, or a compromised/malicious server) capable of advertising a branch ref whose short name begins with `-`, and (2) the victim's GitHub Desktop must fetch from that remote and subsequently trigger checkout of that specific branch (via UI branch-switching, "restore branch" after clone, or similar automatic flows). No admin rights, malware, or credential leakage is needed — only cloning/fetching from an attacker-controlled remote, which is within the defined scope of "attacker controls a cloned/fetched repository ... or a git remote."

### Recommendation
- In `getBranchCheckoutArgs` (`app/src/lib/git/checkout.ts`), insert `--` **before** `branch.name`/`nameWithoutRemote` rather than after, e.g. `['--', branch.name]` (and separately handle the `-b` case so the new branch name is also protected), so git can never interpret these values as flags.
- Additionally/alternatively, validate or reject branch names beginning with `-` when constructing `Branch` objects in `for-each-ref.ts`, consistent with the sanitization already applied to user-typed ref names in `sanitize-ref-name.ts`.

### Proof of Concept
1. Stand up (or simulate via a crafted git server/smart-HTTP responder) a remote that advertises a ref `refs/heads/--upload-pack=touch${IFS}/tmp/pwn` (or an equivalent flag such as `--upload-pack=...`) alongside normal refs.
2. In GitHub Desktop, add this remote and fetch it; `getBranches` (`app/src/lib/git/for-each-ref.ts`) will produce a `Branch` object whose `name` is `--upload-pack=touch${IFS}/tmp/pwn`.
3. Programmatically call `checkoutBranch(repository, thatBranch, null)` (or trigger checkout of the branch through the branch-switcher UI).
4. Observe that the resulting `git` argv is `['checkout', '--upload-pack=touch${IFS}/tmp/pwn', '-b', ..., '--']`, and that git parses the injected token as an option instead of a ref, confirming argument-injection into the checkout invocation.

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

**File:** app/src/lib/git/for-each-ref.ts (L53-61)
```typescript
    const type = ref.fullName.startsWith('refs/heads')
      ? BranchType.Local
      : BranchType.Remote

    const upstream =
      ref.upstreamShortName.length > 0 ? ref.upstreamShortName : null

    branches.push(new Branch(ref.shortName, upstream, tip, type, ref.fullName))
  }
```

**File:** app/src/lib/sanitize-ref-name.ts (L9-11)
```typescript
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```
