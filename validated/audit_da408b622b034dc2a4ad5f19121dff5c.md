## Analog Assessment

The report's broken invariant is: *an external/attacker-influenced data snapshot (oracle price) is consumed later without verifying it is still current, allowing stale data to drive a critical write/decision.* The closest match in GitHub Desktop is in the Copilot merge-conflict auto-resolution feature: a resolution is computed from a **snapshot** of conflicted file content, and later blindly written to disk with only a coarse boolean staleness check that a malicious/attacker-controlled merge (via a crafted fetched branch) can defeat, causing silent corruption of what the user commits.

### Title
Stale Copilot conflict-resolution content is written to disk without verifying the underlying conflict hunks are unchanged - (File: app/src/lib/stores/app-store.ts)

### Summary
`_applyCopilotConflictResolutions` in `app-store.ts` writes a previously computed `resolution.resolvedContent` to disk for each conflicted file, guarding only against the case where the file is **no longer conflicted** at all [1](#0-0) . It never re-checks whether the file's conflict **content/hunks** are still the same as what was fed into `buildConflictContext` when the resolution was generated [2](#0-1) . This is structurally identical to using a Chainlink price without checking `updatedAt`: a value computed at time T is consumed at time T+Δ without validating it is still fresh/valid for the current state.

### Finding Description
`buildConflictContext` reads on-disk conflicted files, extracts conflict hunks, and returns `rawContent`/`hunks` that are sent to Copilot to compute a resolution [2](#0-1) . This happens asynchronously and can take a while (the code tracks `elapsedSeconds > 60/120` for telemetry, implying long-running turns) [3](#0-2) .

Later, when the user clicks "Continue Merge," `_applyCopilotConflictResolutions` iterates the stored `copilotResolutions` and, for each file, only skips the write if the file `isConflictedFileStatus` **and** `!hasUnresolvedConflicts` (i.e., fully externally resolved) [1](#0-0) . There is no comparison of the current on-disk content/hunks against the content that was actually used to generate `resolution.resolvedContent`. If the working tree conflict content changes between snapshot and apply time — e.g. the user re-fetches/re-merges, the operation restarts with a different but still-conflicted base (a remote-controlled merge base or new commits from an attacker-crafted branch reshuffle the conflict hunks while the file remains "conflicted") — the stale AI resolution computed from the old hunks is written verbatim over the new conflict markers and staged with `git add` [4](#0-3) . Because the check only asks "is it still conflicted?" (a boolean) rather than "is it still the *same* conflict?", stale AI-authored content silently overwrites what should be freshly resolved conflict markers, with no diff shown to the user beyond the initial (now outdated) result dialog.

This mirrors the oracle bug precisely: `getPrice` only checked *that a price exists* from `latestRoundData()`, not that it was fresh; here the code only checks *that a conflict exists*, not that it's the same conflict the resolution was computed against.

### Impact Explanation
An attacker who controls a fetched/merged branch (a git remote or GitHub PR head, matching the allowed threat model: "attacker controls a cloned/fetched repository") can shape merge conflicts such that:
- The set of conflicting hunks/regions changes between the Copilot-context snapshot and the apply step (e.g., via concurrent background fetch/refresh of the repository triggering a stale multi-commit-operation state, or a user re-running a merge against updated remote refs before confirming).
- The stale, no-longer-applicable resolution content is written into the user's working tree and immediately `git add`-ed, becoming part of the next commit/push without re-review, since the UI already showed (and the user approved) the earlier, different resolution.

This is "silent corruption of what the user commits or pushes" — an explicitly listed valid impact category. The severity is bounded (it doesn't achieve code execution or credential exfiltration) but does directly corrupt committed content in a way the user did not consent to.

### Likelihood Explanation
Exploitation requires a scenario where the on-disk conflict state changes after the Copilot snapshot was taken but before the user hits "Continue Merge," while the file's conflict status remains "conflicted." This can occur naturally (background operations, retried merges) but demonstrating deterministic attacker control requires further investigation into exactly which flows can mutate `multiCommitOperationState`/working-directory conflicts mid-flight without invalidating `copilotResolutions`. I was not able to fully trace all code paths that populate/refresh `copilotResolutions` (e.g., whether a new merge attempt clears it) within the available tool budget, so likelihood should be treated as **moderate but not fully confirmed** — a Devin session with broader search/build access would be needed to confirm whether `copilotResolutions` is reliably invalidated on every conflict-state change.

### Recommendation
Before writing `resolution.resolvedContent` to disk, recompute or store a fingerprint (e.g., hash) of the original conflicted content/hunks captured in `buildConflictContext`, and compare it against the current on-disk conflict content at apply time. If they differ, treat the file the same as the "externally resolved" case (skip and fall back to manual resolution) rather than silently overwriting, analogous to adding the `updatedAt` staleness check recommended in the oracle report.

### Proof of Concept
Conceptual PoC (not verified end-to-end due to tool limits):
1. Start a merge/rebase against a branch controlled by an attacker (e.g., a PR from a fork) that produces conflicts in `file.txt`.
2. Trigger Copilot conflict resolution; `buildConflictContext` snapshots `file.txt`'s current conflict hunks and Copilot computes `resolvedContent` based on them.
3. While the result dialog is open, cause the working tree's conflict markers in `file.txt` to change (e.g., background fetch + automatic re-merge, or a retried operation) such that `file.txt` is still reported as conflicted (`isConflictedFileStatus` true, `hasUnresolvedConflicts` true) but the actual conflicting content differs from what Copilot resolved.
4. Click "Continue Merge." `_applyCopilotConflictResolutions` passes the staleness guard (file is still "conflicted") and overwrites `file.txt` with the outdated `resolution.resolvedContent`, then stages it via `git add` [5](#0-4) .
5. The user commits/pushes content that does not correspond to the actual, current merge conflict — silent corruption of the commit.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7100-7106)
```typescript
      }
      if (elapsedSeconds > 60) {
        this.statsStore.increment('copilotConflictResolutionOver60sCount')
      }
      if (elapsedSeconds > 120) {
        this.statsStore.increment('copilotConflictResolutionOver120sCount')
      }
```

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```

**File:** app/src/lib/copilot-conflict-context.ts (L429-461)
```typescript
      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
```
