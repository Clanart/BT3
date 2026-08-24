### Title
`popStashEntry` heuristically trusts empty-`stderr` + exit-code-1 as proof of a successful stash apply, causing silent, irreversible deletion of the user's uncommitted changes - (File: `app/src/lib/git/stash.ts`)

### Summary
The report's core invariant break is: *code treats a return signal (bool/exit code) as an unconditional proof of success, when a spec-compliant-but-nonstandard implementation can make that signal lie*. The closest Desktop analog is not a token contract but Git's `stash pop`, whose "communicativeness" (as the code comment itself admits) is similarly unreliable. `popStashEntry` in `app/src/lib/git/stash.ts` decides whether a stash was actually re-applied to the working directory purely by inspecting `exitCode === 1 && stderr.length === 0`, and if that heuristic is true it permanently deletes the stash (the user's only backup of unstaged/uncommitted changes) via `dropDesktopStashEntry`.

### Finding Description
`popStashEntry` runs `git stash pop --quiet <name>` with `expectedErrors: new Set([DugiteError.MergeConflicts])`. [1](#0-0) 

When the command fails, the `.catch` handler does **not** verify that the stash contents actually landed in the working directory. Instead it uses a heuristic explicitly called out in the code's own comments as unreliable ("Not the greatest approach but stash isn't very communicative"): if `exitCode === 1` and `stderr` is empty, it assumes the pop succeeded and immediately calls `dropDesktopStashEntry`, which runs `git stash drop` — an irreversible deletion. [2](#0-1) 

The `git()` wrapper's error classification is itself dependent on parsing `stderr`/`stdout` text against a fixed table of known Git error strings; anything that doesn't match becomes `gitError = null`, i.e. an "unexpected" error that still surfaces through the generic `catch` path used above rather than being distinguished as a real failure. [3](#0-2) 

A cloned/fetched repository is attacker-controlled content from the app's threat model (repo-rules, hooks, filters, merge drivers, `.gitattributes` are all repo-supplied). Git's `stash pop` internally performs a merge of the stash into the working tree, and that merge can invoke repository-configured merge drivers, clean/smudge filters, or `textconv` programs declared in a tracked `.gitattributes`/`.git/config`-referenced script bundled with the clone. Such a filter can:
- write diagnostic/error text to **stdout** instead of stderr (Git does not control what filter/driver scripts print to which stream),
- cause the actual file restoration to fail or apply only partially (e.g. a crafted merge driver that reports success on a subset of paths while `git` overall exits 1),
- while leaving Desktop's specific check `stderr.length === 0` satisfied.

Because the check is a blunt heuristic keyed only on "is stderr empty," instead of verifying that the working directory actually reflects the stash's tree (e.g. by diffing indices/trees or checking `git stash list` for “still needs manual restore” markers), Desktop mis-classifies a failed/partial pop as "popped successfully," and then deletes the only remaining record of the changes with `git stash drop`.

### Impact Explanation
This is silent, unrecoverable data loss of the user's local work: uncommitted stash contents can be destroyed while Desktop displays no error, believing the operation succeeded. This matches "silent corruption of what the user commits or pushes" in spirit — the corrupted artifact here is the user's local working tree/stash rather than a pushed commit, but the causal mechanism (trusting an ambiguous success signal from attacker-influenced tooling) is the direct structural analog of the ERC20 `bool`-trust bug: a boolean/exit-code proxy for success is treated as ground truth without verifying the actual state change.

### Likelihood Explanation
The precondition is that the user clones/fetches a repository (unprivileged, no local/physical access, no prior malware, no admin rights, no leaked credentials) containing a crafted merge driver/clean-smudge filter or hook wired through tracked configuration, and later performs a Desktop-initiated branch switch that uses Desktop's built-in stash-and-restore flow (`popStashEntry`, invoked from `app/src/lib/stores/app-store.ts` and `app/src/ui/dispatcher/dispatcher.ts`) — this is a normal, expected Desktop workflow the app markets as safe ("Desktop stashes your changes for you"), not an unnatural or social-engineered step. The exact reliability of forcing "exit 1 + empty stderr + no actual restoration" depends on specific Git plumbing behavior with custom merge drivers, which I was not able to fully verify by executing Git in this environment — this is a code-level structural weakness (an intentionally-acknowledged unreliable heuristic per the inline comment) rather than a demonstrated end-to-end exploit chain confirmed by a live PoC run.

### Recommendation
Do not infer success from `exitCode`/`stderr` shape alone. After a `stash pop` failure, positively verify the outcome before dropping the stash:
- Check `git stash list` to see if the entry still exists (a truly successful pop removes it automatically); only call `dropDesktopStashEntry` if Git itself did not already drop it.
- Or diff the working tree against the stash's tree object (`tree` field already tracked in `IStashEntry`) to confirm the content was actually restored before deleting the backup.
- Broaden `expectedErrors`/error parsing so that unrecognized-but-fatal failures are surfaced to the user instead of being funneled into the "assume success" branch.

### Proof of Concept
Not independently executed/verified in this session (no local Git execution available). The suggested reproduction outline is:
1. Create a repository with a `.gitattributes` entry wiring a custom merge driver for a tracked file, and a `.git/config`/`core.mergedriver`-style script that:
   - writes an error/warning to **stdout** (not stderr),
   - returns non-zero for the merge of that path, causing `git stash pop` to exit `1` with empty `stderr`,
   - but does not actually restore the pre-stash content of the file to the working directory.
2. Have the user clone this repo in Desktop, make a change, let Desktop stash it (e.g. switching branches), then switch back so Desktop calls `popStashEntry`.
3. Observe: `git stash pop` exits 1 with empty `stderr` → `popStashEntry`'s catch handler treats this as "popped successfully" → calls `dropDesktopStashEntry` → the stash entry is deleted via `git stash drop` even though the file was not actually restored, permanently losing the user's uncommitted changes. [1](#0-0) [3](#0-2)

### Citations

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

**File:** app/src/lib/git/core.ts (L322-333)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }
```
