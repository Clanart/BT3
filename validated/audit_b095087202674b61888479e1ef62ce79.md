Found a legitimate structural analog to the LiFi "unswept intermediate balance" bug: a control that validates *containment* (the "amount doesn't grow" check in LiFi; here, "path stays inside the repo") but never validates that the *specific target* touched is the one the operation was scoped to (the "intermediate token" in LiFi; here, the specific conflicted file the user is resolving). This exists in GitHub Desktop's Copilot merge-conflict resolution feature.

### Title
Copilot conflict-resolution write path trusts model-supplied file paths without restricting them to the actual conflicted-file set, allowing attacker-influenced (prompt-injected) content to be written to arbitrary tracked repo files - ([File: app/src/lib/stores/app-store.ts])

### Summary
When a user resolves a merge/rebase/cherry-pick conflict with Copilot, Desktop builds an LLM prompt that includes commit messages and pull-request titles/bodies pulled from both sides of the merge, which are attacker-controlled if the "theirs" side comes from an untrusted fork/PR/remote. The model's JSON response is parsed into a list of `{path, resolvedContent}` resolutions and, when the user clicks "Continue Merge", `_applyCopilotConflictResolutions` writes `resolvedContent` to `resolution.path` and stages it with `git add`, with no check that `resolution.path` is one of the files that were actually part of the conflict set sent to the model.

### Finding Description
The prompt sent to Copilot embeds untrusted, attacker-controlled text as first-class content: `gatherCommitContext` pulls commit summaries from `theirBranch` [1](#0-0)  and `formatConflictContextForPrompt` splices PR titles/bodies and commit summaries directly into the message sent to the model [2](#0-1) . The system prompt instructs the model to return a `resolutions` array of `{path, hunks, reasoning}` objects [3](#0-2) , but nothing in the schema or validation logic constrains `path` to the original conflicted-file list that was gathered via `getConflictedFiles` and sent to the model [4](#0-3) .

When the user confirms the resolutions, `_applyCopilotConflictResolutions` iterates every `resolution` returned by the model and, for each one, resolves the path within the repository and looks up a matching working-directory entry only to decide whether to *skip* overwriting a file the user already fixed manually — it never uses that lookup as an allowlist gate:

```
const absolutePath = await resolveWithin(repository.path, resolution.path)
if (absolutePath === null) { ...continue }

const onDiskFile = state.changesState.workingDirectory.files.find(
  f => f.path === resolution.path
)
if (
  onDiskFile !== undefined &&
  isConflictedFileStatus(onDiskFile.status) &&
  !hasUnresolvedConflicts(onDiskFile.status)
) {
  continue
}

await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
pathsToStage.push(resolution.path)
``` [5](#0-4) 

If `resolution.path` names a file that isn't in `workingDirectory.files` at all (i.e. any tracked, unmodified file elsewhere in the repository — `.github/workflows/*.yml`, `package.json`, a source file, etc.), `onDiskFile` is `undefined`, the skip condition is `false`, and the write proceeds unconditionally. The only real guard, `resolveWithin`, prevents path traversal outside the repository [6](#0-5)  but does nothing to keep the write scoped to the conflict being resolved. This is exactly the LiFi pattern: a "the delta/target stays within some coarse bound" check (native asset balance staying non-negative; path staying inside the repo) substituting for the actually-needed check ("only the specific token/intermediate balance being swapped"; "only the specific file that was actually in conflict").

### Impact Explanation
An attacker who controls the "theirs" side of a merge (a malicious fork, a crafted PR branch, or a compromised/adversarial remote a victim merges from) can craft commit messages and/or a PR description designed to prompt-inject the conflict-resolution model into emitting an extra `resolutions[]` entry for a path outside the actual conflict set — e.g., a CI workflow file, `package.json`, or a source file the user isn't reviewing as part of "resolving conflicts." Because the write path stages that file automatically (`pathsToStage.push(resolution.path)` → `git add`), the injected content is silently committed as part of the merge the user believed only touched conflicted files. This matches the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Exploitation requires the user to (a) have GitHub Copilot enabled in Desktop, (b) attempt a merge/rebase/cherry-pick against attacker-influenced content (a normal, expected workflow — reviewing/merging a fork PR), and (c) click "Resolve with Copilot" and then "Continue Merge" without manually re-diffing every file the tool claims to have touched. Prompt injection reliability against the specific system prompt/model is the main uncertainty, but the code path itself provides no defense-in-depth if injection succeeds — there is no server-side or client-side allowlist restricting written paths to the conflicted set.

### Recommendation
In `_applyCopilotConflictResolutions`, before writing/staging a resolution, verify `resolution.path` is a member of the `conflictedFiles` set that was actually sent to the model for this resolution session (not just "exists somewhere in the working directory and isn't already clean"). Reject and log/skip any resolution whose path wasn't part of the original conflict set, mirroring the LiFi fix of tracking and enforcing the exact scope (per-token/per-file) rather than a coarse bound (balance non-negative / path-containment).

### Proof of Concept
1. Attacker prepares a fork branch whose latest commit message (or an open PR's title/body targeting the victim's default branch) contains an instruction payload attempting to make the conflict-resolution model emit an additional resolution entry, e.g. targeting `.github/workflows/release.yml` with attacker-chosen `resolvedContent`, alongside legitimate resolutions for the real conflicted files.
2. Victim, using Desktop with Copilot enabled, merges/rebases the attacker's branch, hits real conflicts in unrelated files, and clicks "Resolve with Copilot" then "Continue Merge".
3. `_applyCopilotConflictResolutions` iterates `copilotResolutions`; for the injected `.github/workflows/release.yml` entry, `onDiskFile` lookup returns `undefined` (file wasn't part of the conflict), so the skip condition is false and `writeFile` + `git add` execute, staging attacker-controlled workflow content as part of the merge commit the victim believes only resolved their actual conflicts [5](#0-4) .

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L326-347)
```typescript
export async function gatherCommitContext(
  repository: Repository,
  ourBranch: string,
  theirBranch: string,
  limit: number = 10
): Promise<IConflictCommitContext | null> {
  try {
    const mergeBase = await getMergeBase(repository, ourBranch, theirBranch)
    if (mergeBase === null) {
      return null
    }

    const [ourCommits, theirCommits] = await Promise.all([
      getCommits(repository, `${mergeBase}..${ourBranch}`, limit, undefined, [
        '--first-parent',
      ]),
      getCommits(repository, `${mergeBase}..${theirBranch}`, limit, undefined, [
        '--first-parent',
      ]),
    ])

    return { ourCommits, theirCommits }
```

**File:** app/src/lib/copilot-conflict-context.ts (L482-521)
```typescript
export function formatConflictContextForPrompt(
  context: IConflictResolutionContext
): string {
  const parts: Array<string> = []

  parts.push(
    `Merge conflict between "${context.ourLabel}" (ours) and "${context.theirLabel}" (theirs).`
  )
  parts.push('')

  if (context.pullRequests.length > 0) {
    parts.push('## Pull Request Context')
    parts.push(
      'These pull requests were referenced in the commit history and may explain the intent behind either side:'
    )
    parts.push('')
    for (const pr of context.pullRequests) {
      appendPullRequest(parts, pr)
    }
  }

  if (context.ourCommits.length > 0 || context.theirCommits.length > 0) {
    parts.push('## Recent Commits')
    parts.push('')

    if (context.ourCommits.length > 0) {
      parts.push(`### Ours (${context.ourLabel}) commits:`)
      for (const commit of context.ourCommits) {
        parts.push(`- ${commit.shortSha}: ${commit.summary}`)
      }
      parts.push('')
    }

    if (context.theirCommits.length > 0) {
      parts.push(`### Theirs (${context.theirLabel}) commits:`)
      for (const commit of context.theirCommits) {
        parts.push(`- ${commit.shortSha}: ${commit.summary}`)
      }
      parts.push('')
    }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L218-241)
```typescript
Response format:
{
  "summary": "### Conflicting changes\\n<1-2 sentences: what each side did and where they collided, attributing each to its #PR or short SHA>\\n\\n### Resolution\\n<1 sentence: how you resolved it; if a side was dropped, bold that trade-off>",
  "references": [
    { "type": "pullRequest", "id": "1234" },
    { "type": "commit", "id": "abc1234" }
  ],
  "resolutions": [
    {
      "path": "relative/file/path.ts",
      "hunks": [
        { "resolvedContent": "merged content that replaces conflict 1" },
        { "resolvedContent": "merged content that replaces conflict 2" }
      ],
      "reasoning": "What each side changed in this file, what you kept, and what you dropped or overrode."
    },
    {
      "path": "deleted-or-modified/file.ts",
      "action": "keep",
      "hunks": [],
      "reasoning": "The file was modified with important changes; the deletion was part of an incomplete refactor."
    }
  ]
}
```

**File:** app/src/lib/stores/app-store.ts (L435-435)
```typescript
import { resolveWithin } from '../path'
```

**File:** app/src/lib/stores/app-store.ts (L6549-6570)
```typescript
      const conflictedFiles = getConflictedFiles(
        state.changesState.workingDirectory,
        conflictState.manualResolutions
      )

      if (conflictedFiles.length === 0) {
        log.warn(
          'AppStore: resolveConflictsWithCopilot called with no conflicted files'
        )
        return null
      }

      log.info(
        `[Timing] resolving ${conflictedFiles.length} conflicted file(s)`
      )

      const context = await this.gatherConflictResolutionContext(
        repository,
        labels,
        conflictedFiles,
        state
      )
```

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
