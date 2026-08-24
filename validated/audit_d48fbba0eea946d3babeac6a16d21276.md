Based on my investigation, I found a genuine index-confusion analog to the AuraVault bug in GitHub Desktop's partial-commit/discard diff selection logic.

### Title
Stale/mismatched diff selection indices allow silent inclusion of unintended lines in commits after an attacker-triggered diff refresh - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
The bug class in the report is a "unit confusion": one numeric quantity (rewardPool shares) is used where a different, non-equivalent quantity (AuraVault assets) was required, with no conversion step, silently changing the amount transferred. In Desktop, the analogous quantity is the `DiffSelection` bitmap index (`absoluteIndex`), which is meaningful only relative to one specific `hunks` array. This index is captured by the renderer while the user visually selects lines, then later replayed against a `diff`/`hunks` array to build the actual git patch. If the underlying `hunks` array changes shape (line count/ordering) between the time the user made a selection and the time `formatPatch`/`formatPatchToDiscardChanges` consume it, the same numeric index now points at a semantically different line, but no code detects or blocks this — it just proceeds to generate a patch from the wrong line.

### Finding Description
`DiffSelection` is a plain index-based bitmap with no notion of what a given index represents; it is documented as having "no notion of how many lines exist or what it is that is being selected" [1](#0-0) . Selections are keyed purely by `absoluteIndex = hunk.unifiedDiffStart + lineIndex` [2](#0-1)  and are looked up with `file.selection.isSelected(absoluteIndex)` when constructing the real patch that is passed to `git apply --cached` [3](#0-2) . The same index-based scheme is used for "discard changes" patch generation [4](#0-3) .

Desktop is aware that "the diff might have changed dramatically since last we loaded it" and, on refresh, only prunes indices that no longer correspond to an includable line — it explicitly does *not* verify that a still-valid index still refers to the *same* line of content: [5](#0-4) 

The comment in that code literally states the limitation: "Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines." This means: if a diff refresh (triggered by the working tree changing — e.g. a file the attacker controls via a build step, git hook, file watcher, or another process modifying tracked files while the user is mid-selection) produces a new hunk layout with the *same absolute index* now landing on a different added/deleted line, that index is still considered "selectable" and "selected," and `formatPatch` will happily include whatever text happens to sit at that index in the new hunks — not the line the user actually clicked.

This is structurally identical to the AuraVault flaw: an index/unit from one state ("shares" in the old vault ratio, or here, a selection bitmap position computed against hunk-layout A) is fed directly into an operation expecting the corresponding index in a different state (rewardPool shares 1:1, or here, hunk-layout B) without validating that the mapping still holds.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" from the valid-impact list. A user could stage/commit a partial selection believing they excluded a specific line (e.g., a secret, a debug statement, or a malicious payload they intentionally did not want in the commit), but because the diff was refreshed underneath them with a shifted line layout, an unrelated line (potentially attacker-inserted content, if the modification comes from a build tool, pre-commit hook, or another process writing to the working tree) is committed instead, or a line they intended to keep is silently discarded. Because this happens without any UI error, the user has no signal that a mismatch occurred.

### Likelihood Explanation
This requires: (1) the working tree to change between the user's line selection and the click of "Commit"/"Discard" — plausible via any hook, watcher, or concurrent tool writing to files in the repo (e.g., linters/formatters/build scripts commonly run in a project the user opened in Desktop), and (2) the new diff layout to have the same absolute index but a different logical line, which is not unusual when lines are added/removed near the selected hunk. Desktop's own comment acknowledges the underlying invariant is not preserved. It is not clear from local files whether debounce or explicit "is diff stale" checks elsewhere in the codebase fully mitigate this before commit; I could not find such a stale-check specifically gating `_commitIncludedChanges` or `discardChangesFromSelection` against a changed `diff.text`/hash prior to formatting a patch — the code inspected only reconciles `selectableLines`.

### Recommendation
Before consuming a `DiffSelection` to build a commit or discard patch, validate that the `diff`/`hunks` object used to compute the selection indices is identical (e.g., by comparing `diff.text` or a hash of the parsed hunks) to the one currently being used to format the patch; if they differ, re-derive the selection against the new diff by content (line text/position) rather than raw index, or force a fresh diff/selection round-trip and reject the stale commit/discard attempt with a user-facing error, analogous to how `_changeFileSelection` already guards for concurrent state changes by re-checking `shas`/`file.id` after an async load [6](#0-5) .

### Proof of Concept
1. Open a repository in Desktop with a tracked file, and make an edit that produces multiple hunks.
2. Have an external process (e.g., a `post-checkout`/file-watcher script, or simply another editor/tool) rewrite the file to shift line numbers in one hunk while the user is selecting/deselecting individual lines in the Desktop diff view (this can be automated with an npm/build script or format-on-save tool running in the same repo directory).
3. Trigger a refresh (Desktop's status polling naturally does this periodically); confirm via `app/src/lib/stores/app-store.ts` (`_selectWorkingDirectoryFiles`/`_changeFileLineSelection` path) that only `selectableLines` are pruned — no content-identity check occurs at `start="3478" end="3493"`.
4. Immediately click "Commit"; observe that `formatPatch` (`app/src/lib/patch-formatter.ts` lines 143–170) applies the retained `absoluteIndex` selections against the *new* hunk layout, producing a patch whose actual diff content differs from what was visually selected by the user before the refresh.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L309-319)
```typescript
  /**
   * Returns a copy of this selection instance with a specified set of
   * selectable lines. By default a DiffSelection instance allows selecting
   * all lines (in fact, it has no notion of how many lines exists or what
   * it is that is being selected).
   *
   * If the selection instance lacks a set of selectable lines it can not
   * supply an accurate value from getSelectionType when the selection of
   * all lines have diverged from the default state (since it doesn't know
   * what all lines mean).
   */
```

**File:** app/src/lib/patch-formatter.ts (L143-144)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex
```

**File:** app/src/lib/patch-formatter.ts (L157-170)
```typescript
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
```

**File:** app/src/lib/patch-formatter.ts (L266-308)
```typescript
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
      } else if (selection.isSelected(absoluteIndex)) {
        // Reverse the change (if it was an added line, treat it as removed and vice versa).
        if (line.type === DiffLineType.Add) {
          hunkBuf += `-${line.text.substring(1)}\n`
          newCount++
        } else if (line.type === DiffLineType.Delete) {
          hunkBuf += `+${line.text.substring(1)}\n`
          oldCount++
        } else {
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }

        anyAdditionsOrDeletions = true
      } else {
        if (line.type === DiffLineType.Add) {
          // An unselected added line will stay in the file after discarding the changes,
          // so we just print it untouched on the diff.
          oldCount++
          newCount++
          hunkBuf += ` ${line.text.substring(1)}\n`
        } else if (line.type === DiffLineType.Delete) {
          // An unselected removed line has no impact on this patch since it's not
          // found on the current working copy of the file, so we can ignore it.
          return
        } else {
          // Guarantee that we've covered all the line types.
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }
```

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

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
```typescript
    const selectableLines = new Set<number>()
    if (diff.kind === DiffType.Text || diff.kind === DiffType.LargeText) {
      // The diff might have changed dramatically since last we loaded it.
      // Ideally we would be more clever about validating that any partial
      // selection state is still valid by ensuring that selected lines still
      // exist but for now we'll settle on just updating the selectable lines
      // such that any previously selected line which now no longer exists or
      // has been turned into a context line isn't still selected.
      diff.hunks.forEach(h => {
        h.lines.forEach((line, index) => {
          if (line.isIncludeableLine()) {
            selectableLines.add(h.unifiedDiffStart + index)
          }
        })
      })
    }
```
