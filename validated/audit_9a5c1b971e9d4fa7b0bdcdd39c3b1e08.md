### Title
Silent, Irreversible Loss of Stashed User Changes via Ambiguous `git stash pop` Exit-Code Heuristic - (File: `app/src/lib/git/stash.ts`)

### Summary
The external report's core defect is a **generic failure-handling path that treats an ambiguous/failed outcome as a specific known-good case and then irreversibly disposes of a user-owned asset** (the `on_bounce()` handler assumes any bounce equals "governing action failed" and refunds the entire balance to the wrong party). The direct analog in GitHub Desktop is `popStashEntry()` in `app/src/lib/git/stash.ts`, which assumes that any `git stash pop` failure with exit code `1` and empty `stderr` means "the stash was actually applied successfully, just reported oddly" — and then **permanently deletes** the stash entry (`dropDesktopStashEntry`) based on that unverified assumption, without ever confirming the working directory actually received the stashed changes.

### Finding Description
`popStashEntry()` [1](#0-0)  runs `git stash pop --quiet <name>` and, on failure, applies this heuristic:

```
if (
  e instanceof GitError &&
  e.result.exitCode === 1 &&
  e.result.stderr.length === 0
) {
  log.info(`[popStashEntry] a stash was popped successfully but exit code ${e.result.exitCode} reported.`)
  // bye bye
  return dropDesktopStashEntry(repository, stashSha)
}
return Promise.reject(e)
```

The comment above it admits the ambiguity directly: *"popping a stash that creates conflicts in the working directory reports an exit code of `1` and is not dropped after being applied. So we check for this case and drop it manually unless there's anything in stderr... **Not the greatest approach but stash isn't very communicative**."* [2](#0-1) 

This is structurally identical to the reported bug class:
- The `master`/`elections_master` bounce handler treats *any* bounce (regardless of true cause) as one specific failure mode and takes an irreversible action (refund to initiator) that discards the correct recipients' claim on funds.
- `popStashEntry()` treats *any* exit-code-1/empty-stderr failure as one specific known case ("popped but git reported oddly") and takes an irreversible action (`dropDesktopStashEntry`, i.e., `git stash drop`) that discards the user's only remaining copy of the stashed changes — without verifying the working directory actually contains them.

Exit code `1` with empty `stderr` is not a value Git guarantees to mean "stash applied cleanly." It can also occur for reasons unrelated to a genuine conflict-but-applied outcome — e.g., a `clean`/`smudge`/merge filter defined in a cloned repository's `.gitattributes` (attacker-controlled content, since the repo is what the user cloned/fetched) exiting with status 1 while writing nothing to stderr, or a pre/post-checkout-adjacent merge driver failing silently during the internal 3-way merge that `stash pop` performs. In such a scenario, Desktop's heuristic incorrectly concludes success and calls `dropDesktopStashEntry`, which runs `git stash drop <name>` [3](#0-2)  — permanently deleting the only record of the user's stashed (uncommitted) work, even though it was never actually restored to the working directory.

No verification step exists between the heuristic match and the destructive drop: there is no `git status` check, no diff comparison, nothing confirming the stash's contents actually landed in the working tree before the entry is discarded.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" (and more precisely, silent, irrecoverable loss of uncommitted work) — one of the explicitly valid impact classes. The attacker primitive is a cloned/fetched repository the attacker controls (e.g., via a merge/clean/smudge filter configured in `.gitattributes` or `.git/config` merge drivers referenced by the repo, or content designed to make Git's internal merge machinery exit 1 without stderr output during a `stash pop`). The victim loses uncommitted changes with no warning and no recovery path, since the backing `git stash drop` is not reversible through the Desktop UI (the underlying stash commit becomes unreachable and subject to GC).

### Likelihood Explanation
The path is reachable any time a user pops a Desktop-created stash entry via `dispatcher.popStash()` → `popStashEntry()`, which is a routine, frequently used user action (e.g., "Restore" in the stash UI [4](#0-3) ). The narrow trigger condition (exit code 1, empty stderr, pop not actually fully applied) requires the attacker to control repository content that influences the 3-way merge Git performs internally during `stash pop` (e.g., custom merge/clean filters), which is plausible for a cloned/fetched malicious or compromised repository but not trivially reliable to engineer for arbitrary attacker payloads — hence likelihood is moderate, dependent on crafting Git-internal filter/merge behavior that produces this exact exit signature.

### Recommendation
Do not use exit-code/stderr heuristics to infer success and trigger destructive actions. After a `stash pop` failure, verify actual repository state (e.g., compare working-directory status/diff against the stash's tree, or check that files matching the stash contents were written) before calling `dropDesktopStashEntry`. If verification cannot be performed reliably, prefer failing safe: surface the error to the user and leave the stash entry intact rather than deleting it, mirroring the report's own recommendation to avoid destructive fallback actions when the cause of failure is ambiguous.

### Proof of Concept
Conceptual reproduction (requires validating the precise Git internal condition since this could not be executed in this environment):
1. Clone/create a repository containing a `.gitattributes` entry that registers a custom merge driver (`merge=customdriver` in `.gitattributes`, with `merge.customdriver.driver` configured to exit with status `1` and print nothing to stderr) for a file that will be present in both the stash and the working tree at pop time.
2. In GitHub Desktop, create a stash on that repository (touching the attributed file), then modify the same file on the current branch such that popping the stash triggers the custom merge driver during the internal 3-way merge.
3. Trigger "Restore" on the stash via the Desktop UI, invoking `popStash` → `popStashEntry()`.
4. If `git stash pop` exits with code `1` and empty `stderr` while the merge driver caused the pop to not actually complete correctly, `popStashEntry()`'s catch handler matches the heuristic and calls `dropDesktopStashEntry()`, permanently deleting the stash even though the user's changes were not correctly restored to the working directory.

Note: Exact reproduction requires confirming Git's precise exit/stderr behavior for a failing custom merge driver during `stash pop`, which could not be verified via static code search alone — a Devin session with terminal access would be needed to empirically confirm the exit code/stderr signature.

### Citations

**File:** app/src/lib/git/stash.ts (L219-229)
```typescript
export async function dropDesktopStashEntry(
  repository: Repository,
  stashSha: string
) {
  const entryToDelete = await getStashEntryMatchingSha(repository, stashSha)

  if (entryToDelete !== null) {
    const args = ['stash', 'drop', entryToDelete.name]
    await git(args, repository.path, 'dropStashEntry')
  }
}
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

**File:** app/src/ui/stashing/stash-diff-header.tsx (L95-114)
```typescript
  private onRestoreClick = async () => {
    const { dispatcher, repository, stashEntry } = this.props

    try {
      this.setState({ isRestoring: true })
      await dispatcher.popStash(repository, stashEntry)
    } catch (err) {
      const errorWithMetadata = new ErrorWithMetadata(err, {
        repository: repository,
        retryAction: {
          type: RetryActionType.PopStash,
          stashEntry,
          repository,
        },
      })
      dispatcher.postError(errorWithMetadata)
    } finally {
      this.setState({ isRestoring: false })
    }
  }
```
