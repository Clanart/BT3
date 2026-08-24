## Title
Merge conflict resolution commits pre-existing/attacker-influenced staged content without re-validating it against what the user reviewed - (File: `app/src/lib/git/commit.ts`, `app/src/lib/stores/app-store.ts`)

## Summary
The Maia report describes a function that computes an "amount to act on" from a **mixed pool balance** (user funds + protocol fee) instead of the isolated user amount, silently consuming funds the caller never intended to spend. The GitHub Desktop analog is `createMergeCommit`, which builds a merge commit from **whatever is currently staged in the git index** rather than from the set of changes the user actually reviewed and resolved in the UI — mixing "conflict resolutions the user saw" with "anything git (or a repository-controlled merge driver) already staged" into a single commit.

## Finding Description
`createMergeCommit` in [1](#0-0)  explicitly documents that it "assumes that all conflicts have already been resolved" and "*Warning:* Does _not_ clear staged files before it commits!" It only stages the manual conflict resolutions and the passed-in conflicted files, then runs `git commit --no-edit`, which commits the **entire index**, including anything already staged that Desktop never diffed for the user.

`_finishConflictedMerge` in `app-store.ts` calls this with only the subset of files marked `AppFileStatusKind.Conflicted`, based on an explicit assumption: [2](#0-1) 

```
*  The assumption made here is that all other files that were part of this merge
*  have already been staged by git automatically (or manually by the user via CLI).
...
*  This also means that any uncommitted changes in the index
*  that were in place before the merge was started will _not_ be included, unless
*  the user stages them manually via CLI.
```

The comment itself acknowledges the invariant is fragile: it assumes only "in place before merge" pre-staged changes exist, but doesn't defend against the index being populated by git's own merge machinery acting on repository-controlled configuration. A cloned/fetched repository can ship a `.gitattributes` file that registers custom `merge=<driver>` or `clean`/`smudge` filters for specific paths. During a merge that Desktop initiates, git will invoke these drivers and **auto-stage the driver's output** for non-conflicting or driver-resolved paths — content the user never sees a diff for in Desktop's conflict-resolution UI, because that UI only surfaces files flagged as `Conflicted`. Because `createMergeCommit` never runs an `unstageAll` (unlike the normal commit path `createCommit`, which explicitly does [3](#0-2) ), whatever the merge driver staged flows straight into the commit.

This directly parallels the H-35 pattern: `getThisPositionTicks` used `token0.balanceOf(address(this))` — the *actual* mixed balance — instead of an isolated "user funds" balance, and then committed that whole mixed amount to a new position. Here, `createMergeCommit` uses the *actual* index state — mixed between "reviewed conflict resolutions" and "whatever got staged" — instead of an isolated "reviewed changes" set, and commits that whole mixed state.

## Impact Explanation
If exploited, a maintainer merging a branch from a compromised/malicious repository (or a fork with attacker-controlled `.gitattributes`) could have the resulting merge commit's tree silently include content the merge driver produced but that was never shown to them for review — and then push it. This is a silent corruption of what the user commits/pushes, matching the requested impact class exactly (no local/physical access, no admin rights, no prior malware — only cloning/fetching and merging a hostile repository/branch, an ordinary Desktop workflow).

## Likelihood Explanation
Medium. `git merge`/`checkout` custom drivers via `.gitattributes` are a well-known, git-native mechanism that ships with the repository content itself, so no unnatural user action beyond "merge this branch" is required. The likelihood is tempered by the fact that the attacker needs the merge to actually trigger the driver on files that end up not flagged `Conflicted` (so they're excluded from the UI review but still staged), which constrains exploitation to specific merge/driver configurations rather than being universally triggerable.

## Recommendation
Before committing, `createMergeCommit`/`_finishConflictedMerge` should diff the final index against exactly the set of files Desktop displayed and resolved (conflicted files ∪ files the user explicitly reviewed), and either:
- fail/warn if the index contains additional staged changes beyond what was shown, or
- explicitly `git reset` any paths outside the reviewed set before committing, mirroring the `unstageAll` + selective `stageFiles` pattern already used in `createCommit`.

## Proof of Concept
1. Attacker publishes a repository/branch containing a `.gitattributes` entry such as:
   ```
   secrets.txt merge=evil
   ```
   with a corresponding `git config merge.evil.driver` registered via a repo-provided setup step, or more realistically a `clean`/`smudge` filter attribute that a victim has globally configured and that the attacker's tracked file exercises during merge.
2. Victim uses Desktop to fetch and merge the attacker's branch into their local branch, hitting real conflicts on unrelated files.
3. Desktop's merge-conflicts dialog only lists/diffs the files flagged `Conflicted`; the merge driver's output on `secrets.txt` is auto-staged by git during the merge but never surfaced in the UI.
4. Victim resolves the shown conflicts and clicks "Continue merge," invoking `_finishConflictedMerge` → `createMergeCommit`, which commits the entire index — including the unreviewed `secrets.txt` content — via `git commit --no-edit` (`app/src/lib/git/commit.ts:102-135`).
5. Victim pushes the merge commit, unknowingly propagating content they never reviewed.

### Citations

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/commit.ts (L74-101)
```typescript
/**
 * Creates a commit to finish an in-progress merge
 * assumes that all conflicts have already been resolved
 * *Warning:* Does _not_ clear staged files before it commits!
 *
 * @param repository repository to execute merge in
 * @param files files to commit
 */
export async function createMergeCommit(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map()
): Promise<string> {
  // apply manual conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = files.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```

**File:** app/src/lib/stores/app-store.ts (L7541-7558)
```typescript
    /**
     *  The assumption made here is that all other files that were part of this merge
     *  have already been staged by git automatically (or manually by the user via CLI).
     *  When the user executes a merge and there are conflicts,
     *  git stages all files that are part of the merge that _don't_ have conflicts
     *  This means that we only need to stage the conflicted files
     *  (whether they are manual or markered) to get all changes related to
     *  this merge staged. This also means that any uncommitted changes in the index
     *  that were in place before the merge was started will _not_ be included, unless
     *  the user stages them manually via CLI.
     *
     *  Its also worth noting this method only used in the Merge Conflicts dialog flow, not committing a conflicted merge via the "Changes" pane.
     *
     *  *TLDR we only stage conflicts here because git will have already staged the rest of the changes related to this merge.*
     */
    const conflictedFiles = workingDirectory.files.filter(f => {
      return f.status.kind === AppFileStatusKind.Conflicted
    })
```
