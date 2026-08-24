## Analysis

The NFTX report's broken invariant is: two functions (`mintTo`/`redeemTo`) enforce validation and post-processing on a value, but a third function (`swapTo`) that recombines their internal steps skips those guards — because the guard was attached to the *named operation*, not to the *primitive* (receiving/withdrawing an NFT) itself.

The clearest Desktop analog to this exact "guard attached to one code path but missing from a sibling path touching the same attacker-influenced value" pattern is in the Copilot merge-conflict-resolution feature. `AppStore._applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7269`) is the *write* path for a Copilot-suggested file resolution, and it explicitly guards the resolution's `path` field with `resolveWithin(repository.path, resolution.path)`, rejecting anything that resolves outside the repo: [1](#0-0) 

That check exists specifically because `resolution.path` originates from the AI model's structured output (`IFileResolution.path`, `app/src/lib/copilot-conflict-resolution.ts:27-29`), and the model consumes conflict-hunk text taken directly from the repository's working tree (`buildConflictContext`, which itself contains the same kind of guard for reads: `resolveWithin` in `app/src/lib/copilot-conflict-context.ts:390-407`). Since Copilot's output is influenceable by attacker-controlled repository content (classic prompt-injection surface — a malicious repo can embed instructions in a conflicting file's content that get sent to the model), the `path` field on a resolution object cannot be trusted to stay within the repo, which is exactly why the write path validates it.

However, the same `IFileResolution.path` value is also consumed, unchecked, by the "overflow" context menu in the result dialog: [2](#0-1) 

`onOverflowMenuClick` builds `absolutePath` with a plain `join(repository.path, path)` — no `resolveWithin` — and then wires it to `openFile`, `openFileInExternalEditor`, and `revealInFileManager`. `revealInFileManager` (`app/src/lib/app-shell.ts:61-64`) and `launchExternalEditor`/`launchCustomExternalEditor` (`app/src/lib/editors/launch.ts:61-86`) perform no path containment check of their own — the docstring on `IAppShell.openPath`/`showItemInFolder` even says "Do not use this method with non-validated paths" (`app/src/lib/app-shell.ts:16-40`), and this call site violates that contract.

This satisfies the "valid impact" criteria: the attacker is an unprivileged party controlling only repository content that gets fed to the AI resolver (analogous to controlling a fetched/cloned repo or an "object" surfaced through the app's own feature), and the result is that a path outside the working directory gets passed to `shell.showItemInFolder` / an external editor / the OS default-program opener — an out-of-repo file-system read/reveal triggered from a normal-looking merge-conflict-resolution workflow, bypassing the same containment check that the sibling write path already enforces.

### Title
Copilot conflict-resolution overflow menu opens/reveals attacker-influenced paths outside the repository without the `resolveWithin` containment check used by the write path - (File: app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx)

### Summary
`_applyCopilotConflictResolutions` in `app-store.ts` validates every AI-suggested resolution's `path` field with `resolveWithin` before writing to disk, because that field is derived from model output that can be steered by content embedded in the repository being resolved. The result dialog's overflow menu (`onOverflowMenuClick`) builds the same kind of path with a raw `join(repository.path, path)` and hands it to `openFile`, `openFileInExternalEditor`, and `revealInFileManager` with no equivalent check.

### Finding Description
`IFileResolution.path` (`app/src/lib/copilot-conflict-resolution.ts:27-29`) is populated from the Copilot model's structured JSON output, generated from conflict-hunk text pulled straight out of files in the working directory (`buildConflictContext`, `app/src/lib/copilot-conflict-context.ts`). That data flow is attacker-reachable: a malicious repository can shape the text of a conflicting file (comments, strings, etc.) to attempt to influence what the model returns for the `path`/`hunks` fields of its JSON response (prompt injection). Recognizing this, the code path that actually writes resolved content to disk defends against a malicious `path` by calling `resolveWithin(repository.path, resolution.path)` and skipping the write if it escapes the repo root (`app/src/lib/stores/app-store.ts:7233-7239`).

The `CopilotConflictsDialog`'s overflow menu, rendered for every resolved conflict file in the same result dialog, reuses `resolution`/file `path` values but derives the filesystem path with plain `join()` (`app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx:212-234`), then feeds it to:
- `openFile` (opens with OS default program),
- `openFileInExternalEditor` (spawns the user's configured editor, `app/src/lib/editors/launch.ts:61-86`),
- `revealInFileManager` → `shell.showItemInFolder` (`app/src/lib/app-shell.ts:61-64`).

None of these downstream calls re-validate that the path stays inside the repository; `app-shell.ts` even documents `openPath`/`showItemInFolder` as unsafe for "non-validated paths." The `resolveWithin` guard that the write path relies on is completely absent here, so a `path` value like `../../../../.ssh/id_rsa` or an absolute path would resolve (via `Path.join`) to a location outside the repository and be passed unchecked to the OS shell/editor launch APIs.

### Impact Explanation
If an attacker can get the Copilot resolver to emit a resolution object whose `path` escapes the repository (via prompt injection embedded in conflicting file content in a repo the victim is resolving conflicts against), clicking the corresponding entry's overflow menu in the result dialog can cause GitHub Desktop to open an arbitrary file on the victim's disk in their default program or configured external editor, or reveal it in the file manager — an out-of-repository file read/disclosure triggered by what looks like a normal in-app "open resolved file" action. This is weaker than a silent write (the write path is protected), but it is a genuine containment-check inconsistency between two code paths that consume the exact same attacker-influenced value.

### Likelihood Explanation
Exploitation requires: (1) a successful prompt-injection against the Copilot conflict-resolution model to produce an out-of-repo `path`, and (2) the user opening the result dialog and clicking the overflow menu for that specific file. The first requirement is speculative — I could not verify from the index whether the SDK enforces additional schema constraints on `path` (e.g., restricting it to values from the known conflicted-file list) beyond what's visible in `copilot-conflict-resolution.ts`; that would meaningfully reduce or eliminate likelihood if enforced. The second requirement is a normal, low-friction user interaction within the intended feature, not an "unnatural" step.

### Recommendation
Route the overflow-menu path (and any other UI-only consumer of `resolution.path` / file paths derived from Copilot output) through the same `resolveWithin` check used in `_applyCopilotConflictResolutions` before calling `openFile`, `openFileInExternalEditor`, or `revealInFileManager`, and refuse to render/act on entries whose path fails containment — mirroring the defense already applied to the write path.

### Proof of Concept
Not independently verified against a live SDK response; this is derived from static code review comparing the two consumers of `IFileResolution.path`:
1. `app-store.ts:7233` calls `resolveWithin(repository.path, resolution.path)` and skips the file if it resolves outside the repo.
2. `copilot-conflicts-dialog.tsx:214` calls `join(repository.path, path)` with no equivalent check, then passes the result to `openFile`/`openFileInExternalEditor`/`revealInFileManager`.
A `path` value containing `..` segments or an absolute path would be blocked at (1) but not at (2), demonstrating the asymmetry. Full confirmation that the model can actually be induced to emit such a `path` (i.e., that no upstream schema/allowlist check exists) would require exercising the live Copilot SDK integration, which is outside what static analysis of this index can confirm — I recommend a Devin session with runtime access to trace the exact response schema/validation used by the Copilot SDK call if full exploit confirmation is needed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L212-234)
```typescript
  private onOverflowMenuClick = (path: string) => {
    const { repository, dispatcher, resolvedExternalEditor } = this.props
    const absolutePath = join(repository.path, path)

    const items: IMenuItem[] = []

    if (resolvedExternalEditor !== null) {
      items.push({
        label: `Open in ${resolvedExternalEditor}`,
        action: () => this.props.openFileInExternalEditor(absolutePath),
      })
    }

    items.push(
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absolutePath, dispatcher),
      },
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, path),
      }
    )
```
