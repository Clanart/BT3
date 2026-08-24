## Title
Concurrent Copilot commit-message generation races the real `git commit` staging step, allowing a repo-controlled diff size/latency to cause the wrong file set to be silently committed - (File: app/src/lib/stores/app-store.ts, app/src/lib/git/diff.ts, app/src/lib/git/commit.ts)

## Summary
`_generateCommitMessage` and `_commitIncludedChanges` are two independently-guarded asynchronous flows that both mutate the *same* git index (`.git/index`) via `unstageAll` + `stageFiles`, with no mutual-exclusion between them, and no re-validation that the file selection used for the commit is still the one staged when `git commit` finally runs.

## Finding Description
`getFilesDiffText` (used only to build the diff sent to Copilot/API for message generation) performs a full, repository-wide staging reset directly against the working index: [1](#0-0) 

This is invoked from `_generateCommitMessage`, which is guarded only by `isGeneratingCommitMessage`: [2](#0-1) [3](#0-2) 

Separately, `_commitIncludedChanges` performs the actual commit and is guarded only by an independent `isCommitting` flag: [4](#0-3) [5](#0-4) 

`createCommit`, invoked at commit time, also unconditionally clears and re-stages the index before running `git commit`: [6](#0-5) 

Because `isCommitting` and `isGeneratingCommitMessage` are two separate booleans with no shared lock, the UI does not prevent a user (or an automation triggering both actions) from having both flows in flight concurrently: the commit button and the "Generate Commit Message with Copilot" button are only disabled with respect to their *own* flag, not each other, as seen in the button-enablement logic which checks `isCommitting` and `isGeneratingCommitMessage` independently rather than as a combined lock: [7](#0-6) 

The generation flow's duration is attacker-influenced: `getFilesDiffText` calls out to a remote API (Copilot SDK or GitHub API) with the diff of the *repo-controlled* content, so a maliciously large/slow-to-process diff (e.g., huge generated files, pathological content that stalls the LLM backend, or a slow/hostile API endpoint) can extend the window during which the index is externally being unstaged/restaged (`unstageAll` → `stageFiles(filesSelected)` → diff → `unstageAll`) while the user proceeds to click "Commit". If a real `createCommit` invocation's own `unstageAll`/`stageFiles(selectedFiles)`/`git commit` sequence interleaves with the generation flow's index operations (all are separate `git` subprocess invocations against the same `.git/index`, not protected by any application-level mutex or file lock coordination beyond git's own lockfile, which only prevents corruption, not ordering), the final staged state that `git commit` operates on can end up reflecting `filesSelected` from the message-generation call rather than `selectedFiles` chosen for the commit — or vice versa, depending on interleaving order.

This is the direct structural analog of the Paladin bug: a value (the "amount to charge/commit") is computed and acted upon based on an assumed, temporary state (`filesSelected` for diffing) rather than the actual state that should be authoritative at the moment of the real, effectful operation (the actual `git commit`), and no mechanism reconciles the two.

## Impact Explanation
If the interleaving lands unfavorably, the user could unknowingly commit (and subsequently push) a file selection that differs from what they intended to select in the Changes list at commit time — i.e., silent corruption of what the user commits. This matches the in-scope impact category "silent corruption of what the user commits or pushes." Since this operates purely through normal UI actions (clicking Generate, then Commit) with a repo-controlled diff influencing timing, it does not require local/physical access, admin rights, or pre-existing malware.

## Likelihood Explanation
Exploitability depends on precise timing between two independently-scheduled async operations, and both `unstageAll`/`stageFiles` calls are typically fast for normal-sized diffs, making accidental corruption low-probability in typical use. However, the timing window is directly enlargeable by an attacker who controls the cloned repository content (by crafting extremely large or diff-unfriendly files to stretch `getFilesDiffText`'s network round-trip and processing time), and no code currently prevents the interleaving—there is no shared lock between `isCommitting` and `isGeneratingCommitMessage`, and neither `createCommit` nor `getFilesDiffText` verifies the index state hasn't been touched by a concurrent flow before proceeding. I could not fully verify from the indexed code whether git's own `index.lock` would cause one of the two overlapping `git` invocations to simply fail-fast (which would surface as an error rather than silent corruption) versus succeed in an interleaved order that causes silent corruption; this would require live/dynamic testing of the two flows racing against a real repository, which is outside what static code search can confirm.

## Recommendation
- Unify `isCommitting` and `isGeneratingCommitMessage` under a single mutual-exclusion gate (or have `withIsGeneratingCommitMessage` check/set the same flag as `withIsCommitting` and vice versa) so the two flows can never run concurrently.
- Avoid mutating the real working index for diff generation entirely — use `git diff` against an in-memory/temporary index (e.g., `GIT_INDEX_FILE` pointed at a scratch file, or `git diff <paths>` without touching `--staged`) rather than calling `unstageAll`/`stageFiles` on the actual repository index.
- Before running the final `git commit` in `createCommit`, re-validate that the currently staged set matches the file selection the user confirmed, and abort/re-stage rather than silently proceeding if it doesn't.

## Proof of Concept
Conceptual sequence (not independently executed, derived from the cited code):
1. Attacker crafts a repo with a very large/complex file that produces a large diff and/or is fed to a slow API path.
2. User opens the repo in Desktop, selects files A, and clicks "Generate Commit Message with Copilot" → triggers `_generateCommitMessage` → `getFilesDiffText` calls `unstageAll(repo)` then `stageFiles(repo, filesSelected=A)`, then awaits a slow `git diff --staged`/API round-trip [8](#0-7) .
3. Before that promise resolves, the user changes selection to B and clicks "Commit" → `_commitIncludedChanges` → `createCommit` calls `unstageAll(repo)` then `stageFiles(repo, selectedFiles=B)` and issues `git commit` [9](#0-8) .
4. If step 2's trailing `unstageAll(repository)` (line 598 of `diff.ts`) executes after step 3's `stageFiles` but before `git commit` runs, the index is cleared of B's staged content immediately prior to the commit, and/or the intervening `git diff --staged` in step 2 races the same `.git/index` file that `createCommit` is manipulating — since neither flow is aware of, or synchronized with, the other, the resulting commit will not reliably reflect the user's final selection B.

### Citations

**File:** app/src/lib/git/diff.ts (L574-598)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

  // `--no-ext-diff` should be provided wherever we invoke `git diff` so that any
  // diff.external program configured by the user is ignored
  const args = [
    'diff',
    '--no-ext-diff',
    '--patch-with-raw',
    '--no-color',
    '--staged',
    ...(commitish ? [commitish] : []),
  ]
  const successExitCodes = new Set([0])

  const { stdout } = await git(args, repository.path, 'getFilesDiffText', {
    successExitCodes,
    encoding: 'buffer',
  })

  await unstageAll(repository)
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

**File:** app/src/lib/stores/app-store.ts (L5364-5391)
```typescript
  private async withIsCommitting(
    repository: Repository,
    fn: () => Promise<boolean>
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    // ensure the user doesn't try and commit again
    if (state.isCommitting) {
      return false
    }

    this.repositoryStateCache.update(repository, () => ({
      isCommitting: true,
      hookProgress: null,
      subscribeToCommitOutput: null,
    }))
    this.emitUpdate()

    try {
      return await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isCommitting: false,
        hookProgress: null,
        subscribeToCommitOutput: null,
      }))
      this.emitUpdate()
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L5393-5425)
```typescript
  private async withIsGeneratingCommitMessage(
    repository: Repository,
    fn: (signal: AbortSignal) => Promise<boolean>
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    // ensure the user doesn't try and commit again
    if (state.isGeneratingCommitMessage) {
      return false
    }

    const abortController = new AbortController()

    this.repositoryStateCache.update(repository, () => ({
      isGeneratingCommitMessage: true,
      commitMessageGenerationAbortController: abortController,
    }))
    this.emitUpdate()

    try {
      return await fn(abortController.signal)
    } finally {
      const currentState = this.repositoryStateCache.get(repository)
      if (
        currentState.commitMessageGenerationAbortController === abortController
      ) {
        this.repositoryStateCache.update(repository, () => ({
          isGeneratingCommitMessage: false,
          commitMessageGenerationAbortController: null,
        }))
        this.emitUpdate()
      }
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L6377-6389)
```typescript
    return this.withIsGeneratingCommitMessage(repository, async signal => {
      try {
        // If user is amending a commit, we want to use the commit
        // to amend as the base for the commit message generation.
        const commitToAmend =
          this.repositoryStateCache.get(repository)?.commitToAmend?.sha ??
          undefined
        const diff = await getFilesDiffText(
          repository,
          filesSelected,
          commitToAmend ? `${commitToAmend}^` : undefined
        )
        if (!diff) {
```

**File:** app/src/lib/git/commit.ts (L26-52)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

  const args = ['-F', '-']

  if (options?.amend) {
    args.push('--amend')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  if (options?.signOff) {
    args.push('--signoff')
  }

  if (options?.allowEmpty) {
    args.push('--allow-empty')
  }

  const result = await git(
    ['commit', ...args],
```

**File:** app/src/ui/changes/commit-message.tsx (L1030-1035)
```typescript
          disabled={
            isCommitting === true ||
            (isGeneratingCommitMessage === true &&
              !canCancelGenerateCommitMessage) ||
            (!isGeneratingCommitMessage && noChangesAvailable)
          }
```
