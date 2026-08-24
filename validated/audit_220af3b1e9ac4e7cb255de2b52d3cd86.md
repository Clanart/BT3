This confirms a real pattern in this codebase: many git wrapper functions (`checkout.ts`, `reset.ts`, `rev-list.ts`, `stash.ts`, `submodule.ts`, `diff.ts`, `log.ts`, `reflog.ts`, `apply.ts`, `add.ts`, `clone.ts`, `diff-index.ts`, `rm.ts`) insert a literal `'--'` separator before positional ref/path arguments specifically to stop git from interpreting a leading-dash value as an option. `merge.ts`, however, does not do this: [1](#0-0) 

### Title
Missing `--` separator in `merge()` allows a dash-prefixed branch/ref to be parsed as a git option - (File: app/src/lib/git/merge.ts)

### Summary
`merge()` builds `args = ['merge', ...optionalFlags]` and then does `args.push(branch)` with no `'--'` separator before the positional ref, unlike sibling wrappers (`checkout.ts`, `reset.ts`, `stash.ts`, etc.) that explicitly insert `'--'`. [1](#0-0) 

### Finding Description
If `branch` begins with `-` (e.g. `--upload-pack=/tmp/evil.sh`), it will be appended straight after `git merge [--squash] [--no-verify]` with nothing to stop git's argv parser from treating it as an option rather than a ref, since git only stops option parsing at an explicit `--` token. This breaks the intended invariant that positional ref arguments cannot alter git's own argument parsing.

### Impact Explanation
I could not confirm a reachable attack path where `merge()` is actually called with a fully attacker-controlled string. In this codebase, callers pass `Branch` objects resolved from `git for-each-ref`/`getRemoteHEAD` output (e.g. `contributionTargetDefaultBranch.ref` in `app-store.ts`, or `mergeBranch.ref` in tests) rather than a raw string typed or fetched verbatim from a hostile source. [2](#0-1)  Additionally, Desktop's own ref-name sanitizer strips leading `-`/`+` characters when it constructs new ref names (`sanitizedRefName`), which is evidence the team is already aware of this class of issue and mitigates it at ref-creation time. [3](#0-2)  I was not able to verify within this session whether git's own ref-name validation (`check-ref-format`) rejects a `refs/heads/-foo`-style ref during `fetch`, which would determine whether a malicious remote could actually get such a ref into the local repo's `for-each-ref` output in the first place — this needs deeper testing to say `merge()` is reachable with attacker-supplied dash-prefixed input end-to-end.

Also note `getMergeBase()` in the same file passes commit-ish arguments (`firstCommitish`, `secondCommitish`) with no `'--'` guard either, sharing the same code smell. [4](#0-3) 

### Likelihood Explanation
The `merge` function signature itself does accept an arbitrary `string` for `branch` with no internal validation, so any *future* or currently-unaudited caller passing raw user/attacker input directly would be exploitable. But as currently wired, call sites route through branch-list/ref-resolution machinery rather than passing raw untrusted strings, so real-world exploitability through the identified GUI/PR flows is unconfirmed.

### Recommendation
Add `'--'` before `branch` in `merge()` the same way `checkout.ts`/`reset.ts`/`stash.ts` do, e.g. `args.push('--', branch)`, and likewise harden `getMergeBase()`. This closes the gap defense-in-depth style regardless of whether a concrete attacker-reachable call path currently exists.

### Proof of Concept
```ts
// app/src/lib/git/merge.ts, hypothetical unit test
const args: string[] = []
// simulate git() capturing args
await merge(repository, '--upload-pack=/tmp/evil.sh')
// asserts args === ['merge', '--upload-pack=/tmp/evil.sh']
// instead of the safe ['merge', '--', '--upload-pack=/tmp/evil.sh']
```
This demonstrates the missing `--` guard at the `git()` call in `merge.ts` lines 41–53, but does not by itself prove an end-to-end attacker-controlled path from a malicious remote into `merge()`'s `branch` parameter — that link is unverified.

### Citations

**File:** app/src/lib/git/merge.ts (L41-53)
```typescript
  const args = ['merge']

  if (options?.squash) {
    args.push('--squash')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  args.push(branch)

  const { exitCode, stdout } = await git(args, repository.path, 'merge', {
```

**File:** app/src/lib/git/merge.ts (L106-116)
```typescript
  const process = await git(
    ['merge-base', firstCommitish, secondCommitish],
    repository.path,
    'merge-base',
    {
      // - 1 is returned if a common ancestor cannot be resolved
      // - 128 is returned if a ref cannot be found
      //   "warning: ignoring broken ref refs/remotes/origin/main."
      successExitCodes: new Set([0, 1, 128]),
    }
  )
```

**File:** app/src/ui/app.tsx (L689-700)
```typescript
    this.props.dispatcher.initializeMergeOperation(
      repository,
      false,
      contributionTargetDefaultBranch
    )

    const { mergeStatus } = state.compareState
    this.props.dispatcher.mergeBranch(
      repository,
      contributionTargetDefaultBranch,
      mergeStatus
    )
```

**File:** app/src/lib/sanitize-ref-name.ts (L9-11)
```typescript
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```
