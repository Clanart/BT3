### Title
Ambiguous `heads/` short-ref parsing in `formatAsLocalRef` can cause the branch pruner to resolve to the wrong branch - (File: app/src/lib/git/refs.ts)

### Summary
`formatAsLocalRef` in `app/src/lib/git/refs.ts` uses a naive string-prefix heuristic to fully-qualify a branch's short name into a ref path. It assumes that any name beginning with `heads/` is *only* ever the disambiguation form Git produces when a local branch collides with a same-named tag/remote-tracking ref, and therefore always maps it to `refs/heads/<rest>`. This assumption breaks when the repository also contains an actual local branch whose literal name is `heads/<something>` (a perfectly valid Git ref, `refs/heads/heads/<something>`).

### Finding Description [1](#0-0) 

```
export function formatAsLocalRef(name: string): string {
  if (name.startsWith('heads/')) {
    return `refs/${name}`
  } else if (!name.startsWith('refs/heads/')) {
    return `refs/heads/${name}`
  } else {
    return name
  }
}
```

The comment explains the intent: Git sometimes reports a branch's `refname:short` as `heads/<name>` to disambiguate it from a colliding ref (e.g., a tag with the same name), and in that case the correct fully-qualified ref is `refs/heads/<name>`. That mapping is correct for the disambiguation case.

However, `heads/<name>` is *also* a perfectly legal literal branch name in Git (`git branch heads/foo` is valid), whose real, fully-qualified ref is `refs/heads/heads/foo`. `formatAsLocalRef` cannot distinguish between:
- Git-produced disambiguation output `heads/foo` (meaning `refs/heads/foo`, because branch `foo` collides with e.g. tag `foo`), and
- The literal branch name `heads/foo` (meaning `refs/heads/heads/foo`).

Both inputs are collapsed to `refs/${name}`, i.e. `refs/heads/foo`, in every case — so whenever a repository has both a branch literally named `heads/foo` and an unrelated branch `foo` (or any ref that forces Git to report the short name with an explicit `heads/` prefix), `formatAsLocalRef('heads/foo')` always resolves to `refs/heads/foo`, silently mapping the caller to the *other* branch instead of `refs/heads/heads/foo`.

This function is consumed by:
- `getMergedBranches` in `app/src/lib/git/branch.ts` (to exclude the base branch from its own merged-branches map) [2](#0-1) 
- `BranchPruner.pruneLocalBranches` / `getUpstreamRefForLocalBranchRef` in `app/src/lib/stores/helpers/branch-pruner.ts`, where `formatAsLocalRef` is used to match a `Branch` model's name against canonical (fully-qualified, unambiguous) refs obtained via `%(refname)` from `git branch --merged`, and to decide upstream matching and which local branches are safe to delete. [3](#0-2) 

Because the merged-branch set itself is keyed by full, unambiguous `%(refname)` values (not short names), while the comparison side (`Branch.name` / checked-out-branch names / upstream names) is normalized through the flawed `formatAsLocalRef`, an attacker-controlled repository that plants a branch literally named `heads/<x>` alongside a normal branch `<x>` can cause the equality checks (`formatAsLocalRef(b.name) === ref`) to match the wrong canonical ref.

### Impact Explanation
If exploited through the branch pruner, this can cause GitHub Desktop to:
- Fail to protect a branch the user actually has checked out or that exists in a linked worktree (because `recentlyCheckedOutCanonicalRefs`/`worktreeBranches` matching uses the same broken normalization), leading to **silent deletion of the wrong local branch** (`deleteLocalBranch`).
- Mis-resolve the "upstream" ref for a branch (`getUpstreamRefForLocalBranchRef`), affecting the prune decision (`remoteBranches.includes(upstreamRef)`).

This is a case of "silent corruption of what the user commits or pushes / branch state" driven purely by attacker-controlled repository content (crafted branch names), fitting the in-scope impact category. It does not, on its own, achieve code execution or credential exfiltration — the impact is limited to unexpected local branch deletion/mis-tracking.

### Likelihood Explanation
Exploitation requires the attacker to get the victim to have, in their local repository, both a branch literally named `heads/<x>` and some other ref causing Git to disambiguate a colliding short name to `heads/<x>` at the same time (e.g., a same-named tag) — this is a narrow, contrived naming collision that would not occur in normal repositories. Reaching the vulnerable comparison also depends on the background `BranchPruner` running and the affected branch qualifying as "merged" and past the checkout/worktree exclusion checks. This makes the practical likelihood low, but the underlying logic flaw is real and independently verifiable by unit-testing `formatAsLocalRef` with the two colliding inputs described.

### Recommendation
Do not infer ref qualification from a `heads/` string prefix alone. Instead:
- Prefer obtaining and propagating the full `%(refname)` (already unambiguous) everywhere a canonical ref is needed, rather than re-deriving it from a short name via `formatAsLocalRef`.
- If short-name-to-ref mapping must remain, resolve ambiguity using `git rev-parse --symbolic-full-name` (or equivalent) against the actual repository state instead of a static string heuristic, so that literal `heads/`-prefixed branch names and Git's disambiguation output are never conflated.

### Proof of Concept
Conceptual reproduction (matches the reporter's proof idea):
1. In a test repository, create two local branches: `foo` and `heads/foo` (`git branch heads/foo`), giving refs `refs/heads/foo` and `refs/heads/heads/foo`.
2. Arrange for Git to report the short name of `refs/heads/foo` as `heads/foo` (e.g., by also creating a tag named `foo`, forcing Git's short-name disambiguation to prefix `heads/`).
3. Call `formatAsLocalRef('heads/foo')` from `app/src/lib/git/refs.ts`.
4. Observe the result is always `refs/heads/foo` — correct for the disambiguated `foo` branch, but silently wrong when the same string `heads/foo` is actually the literal branch (`refs/heads/heads/foo`), demonstrating the ambiguity collapses both distinct refs to one qualified ref. [4](#0-3) 
The existing unit tests only cover the disambiguation case and never test for a literal-`heads/`-named branch coexisting with a colliding ref, so this gap is not currently caught by test coverage.

### Citations

**File:** app/src/lib/git/refs.ts (L14-26)
```typescript
export function formatAsLocalRef(name: string): string {
  if (name.startsWith('heads/')) {
    // In some cases, Git will report this name explicitly to distinguish from
    // a remote ref with the same name - this ensures we format it correctly.
    return `refs/${name}`
  } else if (!name.startsWith('refs/heads/')) {
    // By default Git will drop the heads prefix unless absolutely necessary
    // - include this to ensure the ref is fully qualified.
    return `refs/heads/${name}`
  } else {
    return name
  }
}
```

**File:** app/src/lib/git/branch.ts (L183-205)
```typescript
export async function getMergedBranches(
  repository: Repository,
  branchName: string
): Promise<Map<string, string>> {
  const canonicalBranchRef = formatAsLocalRef(branchName)
  const { formatArgs, parse } = createForEachRefParser({
    sha: '%(objectname)',
    canonicalRef: '%(refname)',
  })

  const args = ['branch', ...formatArgs, '--merged', branchName]
  const mergedBranches = new Map<string, string>()
  const { stdout } = await git(args, repository.path, 'mergedBranches')

  for (const branch of parse(stdout)) {
    // Don't include the branch we're using to compare against
    // in the list of branches merged into that branch.
    if (branch.canonicalRef !== canonicalBranchRef) {
      mergedBranches.set(branch.canonicalRef, branch.sha)
    }
  }

  return mergedBranches
```

**File:** app/src/lib/stores/helpers/branch-pruner.ts (L187-284)
```typescript
    // Get all branches checked out within the past 2 weeks
    const twoWeeksAgo = new Date(offsetFromNow(-14, 'days'))
    const recentlyCheckedOutBranches = await getBranchCheckouts(
      this.repository,
      twoWeeksAgo
    )
    const recentlyCheckedOutCanonicalRefs = new Set(
      [...recentlyCheckedOutBranches.keys()].map(formatAsLocalRef)
    )

    // get the locally cached branches of remotes (ie `remotes/origin/main`)
    const remoteBranches = (
      await getBranches(this.repository, `refs/remotes/`)
    ).map(b => formatAsLocalRef(b.name))

    // get branches checked out in linked worktrees so we don't delete them
    const worktreeBranches = new Set(
      (await listWorktrees(this.repository))
        .map(wt => wt.branch)
        .filter(b => b !== null)
    )

    // create list of branches to be pruned
    const branchesReadyForPruning = Array.from(mergedBranches.keys()).filter(
      ref => {
        if (ReservedRefs.includes(ref)) {
          return false
        }
        if (recentlyCheckedOutCanonicalRefs.has(ref)) {
          return false
        }
        if (worktreeBranches.has(ref)) {
          return false
        }
        const upstreamRef = getUpstreamRefForLocalBranchRef(ref, allBranches)
        if (upstreamRef === undefined) {
          return false
        }
        return !remoteBranches.includes(upstreamRef)
      }
    )

    log.info(
      `[BranchPruner] Pruning ${branchesReadyForPruning.length} branches that have been merged into the default branch, ${defaultBranch.name} (${defaultBranch.tip.sha}), from '${this.repository.name}`
    )

    const gitStore = this.gitStoreCache.get(this.repository)
    const branchRefPrefix = `refs/heads/`

    for (const branchCanonicalRef of branchesReadyForPruning) {
      if (!branchCanonicalRef.startsWith(branchRefPrefix)) {
        continue
      }

      const branchName = branchCanonicalRef.substring(branchRefPrefix.length)

      if (options.deleteBranch) {
        const isDeleted = await gitStore.performFailableOperation(() =>
          deleteLocalBranch(this.repository, branchName)
        )

        if (isDeleted) {
          log.info(
            `[BranchPruner] Pruned branch ${branchName} ((was ${mergedBranches.get(
              branchCanonicalRef
            )}))`
          )
        }
      } else {
        log.info(`[BranchPruner] Branch '${branchName}' marked for deletion`)
      }
    }
    this.onPruneCompleted(this.repository).catch(e => {
      log.error(`[BranchPruner] Error calling onPruneCompleted`, e)
    })
  }
}

/**
 * @param ref the canonical ref for a local branch
 * @param allBranches a list of all branches in the Repository model
 * @returns the canonical upstream branch ref or undefined if upstream can't be reliably determined
 */
function getUpstreamRefForLocalBranchRef(
  ref: string,
  allBranches: ReadonlyArray<Branch>
): string | undefined {
  const branch = allBranches.find(b => formatAsLocalRef(b.name) === ref)
  // if we can't find a branch model, we can't determine the ref's upstream
  if (branch === undefined) {
    return undefined
  }
  const { upstream } = branch
  // if there's no upstream in the branch, there's nothing to lookup
  if (upstream === null) {
    return undefined
  }
  return formatAsLocalRef(upstream)
```

**File:** app/test/unit/git/ref-test.ts (L13-21)
```typescript
    it('formats an explicit heads/ prefix', () => {
      const result = formatAsLocalRef('heads/something-important')
      assert.equal(result, 'refs/heads/something-important')
    })

    it('formats when a remote name is included', () => {
      const result = formatAsLocalRef('heads/Microsoft/master')
      assert.equal(result, 'refs/heads/Microsoft/master')
    })
```
