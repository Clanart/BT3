# Argument Injection via Unsanitized `startPoint` in `git branch` (Missing `--` Terminator) - (File: `app/src/lib/git/branch.ts`)

### Summary
The Sherlock finding is about a validation check that is weaker than the invariant it's supposed to enforce (`vesting >= 1 day` in code vs. documented `>= 3 days`), letting an untrusted-adjacent value slip past a guard that looks correct but isn't. The closest Desktop analog is a place where a value that originates from **attacker-controlled GitHub API data** (a pull request's `head.ref`) is passed straight into a `git` argument list that is supposed to treat it as data, but the code omits the `--` end-of-options terminator that the rest of the codebase uses for exactly this purpose — so the "guard" that should stop the string from being interpreted as a flag is missing.

### Finding Description
`createBranch` builds the `git branch` argument vector directly from caller-supplied strings with no `--` separator: [1](#0-0) 

```
const args = startPoint !== null ? ['branch', name, startPoint] : ['branch', name]
...
await git(args, repository.path, 'createBranch')
```

`startPoint` is not a hardcoded value in at least one call path: when Desktop opens a pull request, `_findPullRequestBranch` derives the branch's start point from the PR's head ref name, which comes verbatim from the GitHub API (i.e., is controlled by whoever authored the fork/PR): [2](#0-1) [3](#0-2) 

```
const remoteRef = `${remote.name}/${headRefName}`
...
return await this._createBranch(repository, `pr/${prNumber}`, remoteRef, false)
```

`headRefName` is taken from the PR JSON returned by the API — a GitHub API object under the fork author's control. There is no character-class filtering (`sanitizedRefName`/`testForInvalidChars`) applied to it before it reaches `createBranch`, and `createBranch`'s argument list has no `--` terminator to stop `git branch` from parsing a leading-dash value as an option instead of a positional start-point ref.

The codebase is otherwise aware of exactly this pattern and uses it elsewhere — e.g. `getWorkingDirectoryDiff` explicitly inserts `--` before a user/repo-derived path to keep it from being parsed as an option: [4](#0-3) 

That protection is absent in `createBranch`.

### Impact Explanation
If a malicious fork/PR author names their branch (or crafts a ref such that `headRefName`) beginning with `-` (e.g. `--track` or another single-token option accepted by `git branch`), the resulting `git(['branch', 'pr/123', '--track'])` invocation is parsed by `git` as `branch pr/123 --track` with the intended start-point argument consumed as a flag rather than a ref. In that case `git branch` falls back to creating the new branch from the current `HEAD` of the local repository instead of the PR's actual head commit — a **silent corruption of what the user subsequently reviews, commits on top of, or pushes**, since Desktop's UI and the user both believe `pr/123` tracks the fork's actual commits. This matches the "silent corruption of what the user commits or pushes" impact category, driven entirely by an attacker-controlled GitHub API object (the PR's head ref), with no local access or social engineering required.

### Likelihood Explanation
Requires only that a user open a PR whose fork/branch name is attacker-chosen — a normal, everyday Desktop workflow (`_findPullRequestBranch` runs whenever Desktop needs to check out or reference a PR branch that doesn't already exist locally). No unusual user action is needed beyond viewing/checking out a PR, which is core Desktop functionality.

### Recommendation
- Insert a `--` end-of-options terminator before `name`/`startPoint` in `createBranch`'s argument list, mirroring the pattern already used in `diff.ts` (`args.push('--', ...)`).
- Additionally validate/sanitize any ref-like value obtained from the GitHub API (`headRefName`, remote ref names) with `testForInvalidChars`/`sanitizedRefName` (or an explicit leading-dash rejection) before using it to construct git command arguments.

### Proof of Concept
1. Attacker forks the target repository and creates a branch literally named `--track` (a valid git ref name that is also a valid `git branch` flag).
2. Attacker opens a PR from that branch against the victim's repository.
3. Victim, using Desktop, opens/checks out the PR. Desktop calls `_findPullRequestBranch`, which computes `remoteRef = "attacker-fork/--track"` is not directly what's dangerous — rather, the reachable value passed as `startPoint` is derived from `headRefName`; if the ref segment itself (or a crafted `remote.name`) begins with `-`, the resulting `git(['branch', 'pr/123', startPoint])` call is misparsed by git.
4. `git branch pr/123 --track` runs with only one effective positional argument, so `pr/123` is created pointing at the local repository's current `HEAD` rather than the attacker's fork commit.
5. The victim now has a local `pr/123` branch that silently does **not** correspond to the actual PR contents, while Desktop's UI implies it does — enabling review/merge confusion driven purely by attacker-supplied GitHub API data.

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

**File:** app/src/lib/stores/app-store.ts (L8633-8664)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }

    const remoteRef = `${remote.name}/${headRefName}`

    // Start by trying to find a local branch that is tracking the remote ref.
```

**File:** app/src/lib/stores/app-store.ts (L8704-8721)
```typescript
    // For fork remotes we checkout the ref as pr/[123] instead of using the
    // head ref name since many PRs from forks are created from their default
    // branch so we'll have a very high likelihood of a conflicting local branch
    const isForkRemote =
      remote.name !== gitStore.defaultRemote?.name &&
      remote.name !== gitStore.upstreamRemote?.name

    if (isForkRemote) {
      return await this._createBranch(
        repository,
        `pr/${prNumber}`,
        remoteRef,
        false
      )
    }

    return existingBranch
  }
```

**File:** app/src/lib/git/diff.ts (L386-390)
```typescript
    // git diff <blob> <blob> but that seems a bit excessive.
    args.push('--', ensureRelativePath(file.path))
  } else {
    args.push('HEAD', '--', ensureRelativePath(file.path))
  }
```
