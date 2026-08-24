## Title
`getFilesDiffText` leaves attacker-controlled repository content staged in the index if the intervening `git diff` call throws - (File: `app/src/lib/git/diff.ts`)

## Summary
`getFilesDiffText` (used by Copilot commit-message generation) temporarily clears and re-populates the index (`unstageAll` → `stageFiles` → `git diff --staged` → `unstageAll`) to compute a diff. This mirrors the report's broken invariant: an operation reasons about a resource/state ("clean index, diff computed, index restored") without accounting for the cost/behavior of the intermediate step. If the intermediate `git diff` call throws (its cost/behavior is not fully controlled by Desktop and can be influenced by attacker-controlled file content in the working tree), the second `unstageAll(repository)` call at line 598 never executes, silently leaving files staged that the user never asked to stage.

## Finding Description [1](#0-0) 

The sequence is:
1. `unstageAll(repository)` — clear the index.
2. `stageFiles(repository, files)` — stage only the files selected by the user for commit-message generation.
3. `await git(args, ..., 'getFilesDiffText', { successExitCodes, encoding: 'buffer' })` — run `git diff --staged`.
4. `unstageAll(repository)` — restore the index to its prior (unstaged) state.
5. Only *after* the restore does the code check `stdout.length > 10 * 1024 * 1024` and throw if too large.

Step 3 is a `git()` call whose exceptions are not caught locally. `git()` in `core.ts` can throw for several reasons that are influenced by the *content* of the working directory that git is diffing — for example `ERR_CHILD_PROCESS_STDIO_MAXBUFFER` handling aside, any git process failure (unexpected exit code not in `successExitCodes`, a `GitError`, or a Node `ErrnoException`) surfaces as a thrown error from `git()`. [2](#0-1) [3](#0-2) 

Because the restorative `unstageAll(repository)` at line 598 is placed *after* the `git()` call with no `try/finally`, any exception thrown by that `git diff` invocation skips the cleanup step entirely. The files that were staged purely as a side effect of generating a commit message (which may include files the user did not intend to commit at that moment, or files whose content is influenced by an untrusted source such as a fetched/cloned branch) remain staged in the index after the function throws.

The caller (`_generateCommitMessage` in `app-store.ts`) wraps the call in a try/catch that only reports the error to the user via `emitError`/`ErrorWithMetadata`; it does not attempt to restore the index. [4](#0-3)  There is no guard elsewhere in the commit flow that re-verifies staged-vs-selected file sets before a subsequent commit action, so a later "Commit" click by the user would commit whatever the index happens to contain at that time — which now silently includes files beyond what the user selected in the Changes list, because the temporary staging performed for diff computation was never undone.

## Impact Explanation
This is a "silent corruption of what the user commits or pushes" scenario: the index (a resource whose state directly controls what `git commit` will record) is mutated by an internal helper based on an assumption ("the diff step is cheap/quick and can be reasoned about linearly") that does not hold once that step can throw. An attacker who controls repository content (e.g., a file that reliably makes `git diff --staged` fail for that path — pathological content, permission/mode issues, or any condition that yields a non-zero/unexpected exit code from `git diff`) can cause the cleanup step to be skipped, leaving unintended files staged. The next commit made by the user in that repository could then include content the user never selected in the Changes list, i.e., a silent corruption of what gets committed/pushed — matching the report's "grief" pattern where an unaccounted-for cost between a check and its consumption invalidates a downstream guarantee.

## Likelihood Explanation
The likelihood is constrained by the difficulty of reliably making `git diff --staged` throw for attacker-chosen content while the file is otherwise a plausible tracked file the victim would select for commit-message generation. I was not able to fully verify a concrete, reproducible trigger for the intermediate `git()` call throwing (e.g., a specific file content/mode/encoding combination that `dugite`/git reliably rejects with a non-zero exit code outside `successExitCodes`), which is necessary to rate confidence in exploitability higher. This is a real code-path gap (missing `try/finally` around a stage/unstage pair), but without confirming a concrete git-level trigger, likelihood should be treated as uncertain/moderate rather than confirmed-high.

## Recommendation
Wrap the stage → diff → unstage sequence in `getFilesDiffText` in a `try/finally` so that `unstageAll(repository)` is guaranteed to run regardless of whether the `git diff` call throws, e.g.:
```ts
await unstageAll(repository)
await stageFiles(repository, files)
try {
  const { stdout } = await git(args, repository.path, 'getFilesDiffText', { successExitCodes, encoding: 'buffer' })
  ...
} finally {
  await unstageAll(repository)
}
```
This removes the intermediate-step assumption entirely rather than trying to bound its cost, consistent with the report's long-term recommendation to avoid relying on a value/state that can be invalidated by unaccounted work.

## Proof of Concept
I could not construct or verify an end-to-end reproduction (a concrete file/content that makes `git diff --staged` throw inside `getFilesDiffText`) within the available tooling/index. The structural gap — the missing `try/finally` around `unstageAll`/`stageFiles`/`git diff`/`unstageAll` at `app/src/lib/git/diff.ts:569-608` — is directly verifiable from source, but confirming a real-world trigger for the intermediate throw would require running the app/git locally, which is outside the scope of this read-only analysis. Flagging this explicitly as unverified rather than asserting a working exploit.

### Citations

**File:** app/src/lib/git/diff.ts (L569-608)
```typescript
export async function getFilesDiffText(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  commitish?: string
): Promise<string> {
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

  // No more than 10MB
  if (stdout.length > 10 * 1024 * 1024) {
    throw new Error('Diff is too large to render')
  }

  // `.toString()` in a promise in case its a large buffer
  const outputString = await (async () => stdout.toString('utf8'))()
  return outputString
}
```

**File:** app/src/lib/git/core.ts (L296-320)
```typescript
          ).catch(err => {
            // If this is an exception thrown by Node.js (as opposed to
            // dugite) let's keep the salient details but include the name of
            // the operation.
            if (isErrnoException(err)) {
              throw new Error(`Failed to execute ${name}: ${err.code}`)
            }

            if (isMaxBufferExceededError(err)) {
              throw new ExecError(
                `${err.message} for ${name}`,
                err.stdout,
                err.stderr,
                // Dugite stores the original Node error in the cause property, by
                // passing that along we ensure that all we're doing here is
                // changing the error message (and capping the stack but that's
                // okay since we know exactly where this error is coming from).
                // The null coalescing here is a safety net in case dugite's
                // behavior changes from underneath us.
                err.cause ?? err
              )
            }

            throw err
          })
```

**File:** app/src/lib/git/core.ts (L346-366)
```typescript
          let acceptableError = true
          if (gitError !== null && opts.expectedErrors) {
            acceptableError = opts.expectedErrors.has(gitError)
          }

          if ((gitError !== null && acceptableError) || acceptableExitCode) {
            return gitResult
          }

          // The caller should either handle this error, or expect that exit code.
          const errorMessage = new Array<string>()
          errorMessage.push(
            `\`git ${args.join(
              ' '
            )}\` exited with an unexpected code: ${exitCode}.`
          )

          const terminalOutput = terminalChunks.join('')

          if (terminalOutput.length > 0) {
            // Leave even less of the combined output in the log
```

**File:** app/src/lib/stores/app-store.ts (L6377-6430)
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
          return false
        }

        const response = enableCopilotSdkCommitMessageGeneration(account)
          ? await this.copilotStore.generateCommitMessage(
              account,
              diff,
              repository.path,
              await this.resolveCopilotModelRequest(
                this.getSelectedCopilotModels(account)[
                  'commit-message-generation'
                ] ?? null
              ),
              this.repositoryStateCache
                .get(repository)
                ?.changesState.currentRepoRulesInfo?.commitMessagePatterns.getRules() ??
                [],
              signal
            )
          : await API.fromAccount(account).getDiffChangesCommitMessage(diff)

        this._setCommitMessage(repository, {
          summary: response.title,
          description: response.description,
          timestamp: Date.now(),
          generatedByCopilot: true,
        })

        this.statsStore.increment('generateCommitMessageCount')
      } catch (e) {
        if (e instanceof CommitMessageGenerationCancelledError) {
          return false
        }

        this.emitError(
          new ErrorWithMetadata(e, {
            repository,
          })
        )
        return false
      }
```
