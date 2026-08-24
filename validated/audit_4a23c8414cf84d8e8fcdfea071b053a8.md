### Title
Silent stash-success assumption can desync Desktop's stash bookkeeping when switching branches into/out of a crafted "unborn" repository state - (File: `app/src/lib/git/stash.ts`)

### Summary
`createDesktopStashEntry` in `app/src/lib/git/stash.ts` does not reliably check whether `git stash push` actually succeeded; it infers success from exit code and stderr content instead of verifying a real result value, which is the same "unchecked return value" bug class as the original report (transfer success inferred instead of verified).

### Finding Description
`createDesktopStashEntry` runs `git stash push -m <message>` and, on a non-zero exit, applies a heuristic instead of a hard check: [1](#0-0) 

The comment in the code itself documents that this heuristic is known to be wrong in at least one reachable case — an "unborn" repository (no initial commit yet):
```
// running git stash push in an unborn repository will get you an exit code of 1
// but no stash was created:
// % git stash push -m foo ; echo $?
// You do not have the initial commit yet
// 1
```
Because the message starts with `You do not have...` rather than `error: `, `errorPrefixRe` does not match, so the `catch` handler falls into the "assume success" branch, logs an informational message, and returns `e.result` as if the stash succeeded. The caller (`createDesktopStashEntry`) then returns `true`, telling Desktop that the user's working-directory changes are safely stashed — mirroring how the C4 report's contracts treated a failed/no-op `transfer` as a completed one and continued their accounting.

`popStashEntry` has a similar heuristic in the other direction: an exit code of `1` with empty stderr is treated as "stash was actually popped but Git misreported it," and the code proactively calls `dropDesktopStashEntry` to delete the corresponding stash ref: [2](#0-1) 

Both functions never verify the real git-level truth (e.g., re-querying `refs/stash` before/after) — they infer state transitions from exit-code/stderr heuristics, exactly like inferring a successful `transfer` from a truthy call instead of checking the boolean return value.

### Impact Explanation
Desktop's higher-level branch-switch/checkout flow (`app-store.ts`, which calls `createDesktopStashEntry`/`popStashEntry`/`dropDesktopStashEntry` in more than a dozen places) relies on the boolean/void result of these functions to decide whether it is safe to proceed with a checkout and whether a stash needs to be restored or dropped afterward. If `createDesktopStashEntry` returns `true` when no stash was actually created:
- Desktop's internal bookkeeping (which stash SHA belongs to which branch) becomes desynchronized from the real `refs/stash` reflog.
- A later `popStashEntry`/`dropDesktopStashEntry` call matching "by SHA" can act on the wrong (or a stale, previously existing) desktop stash entry, or silently no-op, leaving Desktop's model of "your changes are safe in a stash" incorrect.
- This can result in silent loss of the "restore point" for the user's uncommitted changes across a branch switch — a corruption of the state that is later "committed" (restored) back into the working directory — without any error surfaced to the user, satisfying the "silent corruption of what the user commits" impact class from the task brief.

This is Medium-severity by the same logic the original judge used: no direct fund/asset loss, but the availability/correctness of a core Desktop safety mechanism (auto-stash around checkout) can be silently compromised under external conditions (an attacker-crafted repository state).

### Likelihood Explanation
The triggering condition — a repository whose current branch is "unborn" (no initial commit) — is fully attacker-controllable: a malicious/crafted repository's default branch can be an empty/orphan branch. A user cloning such a repository, making local edits, and then switching to another branch (a normal, unmodified user workflow, not requiring local/admin access or social engineering) would exercise this exact code path, since Desktop auto-stashes changes before checkout. The specific exit-code/stderr behavior for this scenario is explicitly documented by the developers as unresolved ("Here be dragons... I'm not going to mess with this now"), which increases confidence that the edge case is real and unhandled rather than purely theoretical. However, I could not fully trace every downstream `app-store.ts` call site in this pass to confirm whether every checkout path additionally protects against data loss with git's own tracked-file overwrite guard, so the "loss of user changes" portion of the impact is partially uncertain, while the "stash bookkeeping desync" portion is directly evidenced in code.

### Recommendation
Replace the exit-code/stderr heuristics in `createDesktopStashEntry` and `popStashEntry` with an authoritative check: after a `stash push`/`stash pop` call that doesn't cleanly succeed, re-query `refs/stash` (e.g., via `getStashes`) to determine whether the expected stash entry actually exists/was removed, rather than inferring success from the absence of `error:`-prefixed stderr lines or from exit code alone. This mirrors using `safeTransfer`-style verified outcomes instead of trusting a return code that can be misleading.

### Proof of Concept
1. Create a bare/local repository whose default branch has zero commits (`git init`, do not commit) and expose it as a clone target (simulating an attacker-hosted repo with an "unborn" default branch, e.g., via an orphan branch used as HEAD).
2. In GitHub Desktop, clone the repository, create some uncommitted local changes on the unborn branch.
3. Trigger a branch switch to another branch in the same repo (Desktop will call `createDesktopStashEntry` internally as part of its "stash before checkout" flow).
4. Because `git stash push -m ...` on an unborn HEAD returns exit code `1` with stderr `You do not have the initial commit yet` (no `error: ` prefix), `createDesktopStashEntry`'s catch handler treats this as a successful stash (per the documented behavior at `app/src/lib/git/stash.ts:161-199`) and returns `true`, even though no entry was added to `refs/stash`.
5. Desktop proceeds believing a protective stash exists; subsequent stash-related bookkeeping (pop/drop by SHA) operates on a nonexistent/desynchronized entry, demonstrating the silent-failure-to-accounting-corruption path without requiring local/admin access.

### Citations

**File:** app/src/lib/git/stash.ts (L161-199)
```typescript
  const result = await git(args, repository.path, 'createStashEntry').catch(
    e => {
      // Note: 2024: Here be dragons. As I converted this code to get rid of the
      // successExitCode use I got curious about the assumptions made in the
      // following logic. It assumes that as long as the exit code for `git
      // stash push` is 1 and there are no lines beginning with "error: " then
      // a stash was created. That didn't hold up to a quick read of the stash
      // code. For example, running git stash push in an unborn repository will
      // get you an exit code of 1 but no stash was created:
      //
      // % git stash push -m foo ; echo $?
      // You do not have the initial commit yet
      // 1
      //
      // I'm not going to mess with this now but I felt the need to document
      // my findings should I or any other brave soul choose to tackle this in
      // the future.
      if (e instanceof GitError && e.result.exitCode === 1) {
        // search for any line starting with `error:` -  /m here to ensure this is
        // applied to each line, without needing to split the text
        const errorPrefixRe = /^error: /m

        const matches = errorPrefixRe.exec(coerceToString(e.result.stderr))
        if (matches !== null && matches.length > 0) {
          // rethrow, because these messages should prevent the stash from being created
          return Promise.reject(e)
        }

        // if no error messages were emitted by Git, we should log but continue because
        // a valid stash was created and this should not interfere with the checkout

        log.info(
          `[createDesktopStashEntry] a stash was created successfully but exit code ${result.exitCode} reported. stderr: ${result.stderr}`
        )
        return e.result
      }
      return Promise.reject(e)
    }
  )
```

**File:** app/src/lib/git/stash.ts (L238-271)
```typescript
export async function popStashEntry(
  repository: Repository,
  stashSha: string
): Promise<void> {
  // ignoring these git errors for now, this will change when we start
  // implementing the stash conflict flow
  const expectedErrors = new Set<DugiteError>([DugiteError.MergeConflicts])
  const stashToPop = await getStashEntryMatchingSha(repository, stashSha)

  if (stashToPop !== null) {
    const args = ['stash', 'pop', '--quiet', `${stashToPop.name}`]
    await git(args, repository.path, 'popStashEntry', {
      expectedErrors,
    }).catch(e => {
      // popping a stashes that create conflicts in the working directory
      // report an exit code of `1` and are not dropped after being applied.
      // so, we check for this case and drop them manually unless there's
      // anything in stderr as that could have prevented the stash from being
      // popped. Not the greatest approach but stash isn't very communicative
      if (
        e instanceof GitError &&
        e.result.exitCode === 1 &&
        e.result.stderr.length === 0
      ) {
        log.info(
          `[popStashEntry] a stash was popped successfully but exit code ${e.result.exitCode} reported.`
        )
        // bye bye
        return dropDesktopStashEntry(repository, stashSha)
      }
      return Promise.reject(e)
    })
  }
}
```
