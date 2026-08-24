### Title
Line-index-based partial commit selection is stale-checked against the wrong diff, allowing background repository mutation to silently commit unreviewed content - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user review a diff, select individual lines/hunks for a partial commit, and click "Commit". The selection the user makes is stored as absolute line-index positions (`DiffSelection`) against the diff that was displayed at selection time. When the commit actually executes, the patch that is sent to `git apply --cached` is *not* built from that displayed diff — it is rebuilt from a freshly re-read working-directory diff at commit time. If the file content on disk changes between the time the user reviewed/selected lines and the time the commit runs, the line indices no longer correspond to the same diff content, and `git apply` will apply the user's selection mask against different hunks/lines than what was shown to them. This mirrors the reported "stale calculation used at commit time" race in `update_total_balance_callback`: a value calculated at time T1 is used unchecked at time T2, after state has changed.

### Finding Description
`WorkingDirectoryFileChange.selection` (a `DiffSelection`) is created against absolute line indices of a specific diff object that was fetched once and shown in the UI (e.g. via the Changes list diff viewer). Several async flows in `app-store.ts` already recognize the risk of a stale diff and guard against it by re-comparing state before/after an await — see the explicit staleness checks in `_changeFileSelection` and `updateChangesStashDiff`: [1](#0-0) [2](#0-1) 

However, `_commitIncludedChanges` — the function that actually performs a commit of the user's selected changes — captures `selectedFiles` synchronously from `state.changesState.workingDirectory.files` and passes them straight into `createCommit` without ever re-validating that the file's `selection` (line indices) still corresponds to the current on-disk diff: [3](#0-2) 

`createCommit` stages the selected files via `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex`: [4](#0-3) [5](#0-4) 

Critically, `applyPatchToIndex` re-fetches the diff **fresh from disk** at commit time (not the diff the user actually looked at), then formats a patch using the stale `file.selection` (absolute line indices) against this newly fetched diff, and applies it to the index with `git apply --cached`: [6](#0-5) 

`formatPatch`/`patch-formatter.ts` blindly trusts `file.selection.isSelected(absoluteIndex)` against whatever hunks/lines are present in the (potentially different) diff passed in: [7](#0-6) 

The corrupted value here is the **relationship between `file.selection`'s line indices and the diff hunks they are matched against**. Unlike `_changeFileSelection`/`updateChangesStashDiff`, `_commitIncludedChanges` has no "has anything changed since the user built this selection" guard, so nothing stops a race between (a) the moment the diff was rendered and the user picked lines, and (b) the moment `applyPatchToIndex` re-reads the file and reapplies those indices.

The working directory is attacker-influenceable in normal Desktop use because Desktop periodically performs background operations that mutate tracked files without direct user action, most notably background fetch + fast-forward of the current branch (`performFetch` → `fastForwardBranches` → `_refreshRepository`), driven by `BackgroundFetcher`: [8](#0-7) [9](#0-8) 

If the current branch can be fast-forwarded (e.g. a remote/attacker-influenced upstream the user is tracking pushes new content, or a proxy/MITM response injects a fast-forwardable commit), `fastForwardBranches` rewrites the working tree files while the user still has an open partial-selection based on the pre-fast-forward diff. There is no re-validation before the subsequent commit.

### Impact Explanation
An attacker who controls the content of a remote/tracking branch the user has fetched (a normal, unprivileged capability for any git remote/collaborator/fork owner) can cause the user's next partial commit to silently include or exclude different lines than the ones the user actually reviewed and selected. This is a **silent corruption of what the user commits**: the user believes they are committing only the lines they checked, but the applied patch is computed from a different underlying diff than the one displayed, so the resulting commit (and any subsequent push) can contain unreviewed/unintended content — potentially reintroducing content the user explicitly deselected (e.g., secrets, debug code, or malicious payloads the user thought they excluded) or dropping content the user intended to include. Because this happens without any error or warning, the user has no signal that the commit doesn't match their review.

### Likelihood Explanation
This requires no local/physical access, no admin rights, and no prior compromise — only that the attacker controls (or can influence via MITM/proxy) a remote the victim has configured for background fetch/fast-forward, and that the timing window between diff review and commit overlaps with a background fetch cycle. `BackgroundFetcher` runs automatically and periodically for any repository with a linked GitHub repository, and `fastForwardBranches` runs unconditionally as part of every fetch (background or user-initiated) without checking whether the user has an in-progress partial selection. The race window is realistic for slower reviews of larger diffs, or repeated automated fetch cycles (default interval is on the order of an hour but can be shortened by server-provided cache headers per `DefaultFetchInterval`/`MinimumInterval`), and the underlying vulnerable code path (`_commitIncludedChanges` lacking any staleness check) is unconditional — no special conditions in the app's own logic prevent it.

### Recommendation
1. In `_commitIncludedChanges`, snapshot an identity for each selected file's diff (e.g., the diff's content hash or the underlying blob hash used to build the `DiffSelection`) at selection time, and re-validate that identity against the current on-disk file directly before staging — mirroring the guard pattern already used in `_changeFileSelection` and `updateChangesStashDiff`.
2. In `applyPatchToIndex`/`stageFiles`, refuse to apply a `DiffSelection` if the diff it was derived from does not match the diff freshly loaded immediately before staging (e.g., compare hunk headers/line counts, or store and check the diff's line/hash fingerprint on `DiffSelection` itself).
3. As a defense-in-depth measure, prevent background fast-forwards (or defer them) while a commit operation with partial file selections is in flight (there is already a `withIsCommitting` guard used for the analogous "don't allow concurrent commits" case that could be extended to gate background fetch's fast-forward step).

### Proof of Concept
1. Victim has a GitHub repository open in Desktop, tracking a remote branch that a collaborator (or MITM'd proxy) can push to.
2. Victim modifies a large tracked file locally and opens the Changes view; Desktop computes and displays a diff, and the victim selects only some lines for inclusion (leaving out, say, a line containing a secret) — this selection is stored as absolute line indices into the currently-displayed diff.
3. While the victim is still reviewing/selecting (a window of tens of seconds to minutes is realistic for larger diffs), Desktop's `BackgroundFetcher` triggers `_fetch` → `performFetch` → `fastForwardBranches`, which the attacker has arranged to be fast-forwardable by pushing new commits to the tracked upstream branch that touch the same file, changing its line offsets on disk.
4. Victim clicks "Commit." `_commitIncludedChanges` reads the stale `selectedFiles` (with old line indices) and calls `createCommit` → `stageFiles` → `applyPatchToIndex`, which re-reads the (now different) diff from disk and applies the stale line-index selection to it.
5. The resulting commit's actual content differs from what the user selected/reviewed (e.g., the secret line the user meant to exclude is now included, or vice versa), with no error or warning shown to the user.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2099-2114)
```typescript
    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const { shas: shasAfter } = stateAfterLoad.commitSelection
    // A whole bunch of things could have happened since we initiated the diff load
    if (
      shasAfter.length !== shas.length ||
      !shas.every((sha, i) => sha === shasAfter[i])
    ) {
      return
    }

    if (!stateAfterLoad.commitSelection.file) {
      return
    }
    if (stateAfterLoad.commitSelection.file.id !== file.id) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3656-3668)
```typescript
    const diff = await getCommitDiff(repository, file, file.commitish)

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesStateAfterLoad = stateAfterLoad.changesState

    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3681-3714)
```typescript
  public async _commitIncludedChanges(
    repository: Repository,
    context: ICommitContext
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })

    const gitStore = this.gitStoreCache.get(repository)

    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
        },
        { gitContext: { kind: 'commit' }, repository }
      )
```

**File:** app/src/lib/stores/app-store.ts (L5924-5977)
```typescript
  private async performFetch(
    repository: Repository,
    fetchType: FetchType,
    remotes?: IRemote[]
  ): Promise<void> {
    await this.withPushPullFetch(repository, async () => {
      const gitStore = this.gitStoreCache.get(repository)

      try {
        const fetchWeight = 0.9
        const refreshWeight = 0.1
        const isBackgroundTask = fetchType === FetchType.BackgroundTask

        const progressCallback = (progress: IFetchProgress) => {
          this.updatePushPullFetchProgress(repository, {
            ...progress,
            value: progress.value * fetchWeight,
          })
        }

        if (remotes === undefined) {
          await gitStore.fetch(isBackgroundTask, progressCallback)
        } else {
          await gitStore.fetchRemotes(
            remotes,
            isBackgroundTask,
            progressCallback
          )
        }

        const refreshTitle = __DARWIN__
          ? 'Refreshing Repository'
          : 'Refreshing repository'

        this.updatePushPullFetchProgress(repository, {
          kind: 'generic',
          title: refreshTitle,
          description: 'Fast-forwarding branches',
          value: fetchWeight,
        })

        await this.fastForwardBranches(repository)

        this.updatePushPullFetchProgress(repository, {
          kind: 'generic',
          title: refreshTitle,
          value: fetchWeight + refreshWeight * 0.5,
        })

        // manually refresh branch protections after the push, to ensure
        // any new branch will immediately report as protected
        await this.refreshBranchProtectionState(repository)

        await this._refreshRepository(repository)
```

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

**File:** app/src/lib/git/apply.ts (L52-82)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })

```

**File:** app/src/lib/patch-formatter.ts (L129-161)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L77-116)
```typescript
  /** Perform a fetch and schedule the next one. */
  private async performAndScheduleFetch(
    repository: GitHubRepository
  ): Promise<void> {
    if (this.stopped) {
      return
    }

    const shouldFetch = await this.shouldPerformFetch(this.repository)

    if (this.stopped) {
      return
    }

    if (shouldFetch) {
      try {
        await this.fetch(this.repository)
      } catch (e) {
        const ghRepo = this.repository.gitHubRepository
        const repoName =
          ghRepo !== null ? ghRepo.fullName : this.repository.name

        log.error(`Error performing periodic fetch for '${repoName}'`, e)
      }
    }

    if (this.stopped) {
      return
    }

    const interval = await this.getFetchInterval(repository)
    if (this.stopped) {
      return
    }

    this.timeoutHandle = window.setTimeout(
      () => this.performAndScheduleFetch(repository),
      interval
    )
  }
```
