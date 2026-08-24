Based on my research, I found a plausible analog in the newly-added Copilot merge-conflict-resolution feature, but I want to be upfront about the confidence level given my remaining tool budget: I was not able to fully trace whether `app/src/lib/stores/copilot-store.ts` or `app/src/lib/copilot-conflict-resolution.ts` perform any character sanitization on the model's `reasoning`/`markdown` output before it reaches the dialog components. My analysis below is based on what I could confirm in the UI-layer code.

### Title
Unsanitized, repository-derived Copilot conflict-resolution text (`reasoning`, `summary.markdown`) can misrepresent which side of a conflict was kept before the user confirms - ([File: app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx])

### Summary
GitHub Desktop's Copilot-assisted conflict resolution flow shows a confirmation dialog (`CopilotConflictsDialog`) summarizing, per conflicted file, which side ("ours"/"theirs"/"copilot") was kept and why, before the user clicks "Continue" to finalize a merge/rebase/cherry-pick/squash. The `reasoning` string is rendered directly into the DOM as plain text (`{reasoningText}`), and a separate model-authored `summary.markdown` field is rendered via `SandboxedMarkdown` (which does apply `DOMPurify`/`marked` sanitization). Neither path visibly strips ASCII control characters or Unicode bidirectional-override characters (e.g. U+202E RIGHT-TO-LEFT OVERRIDE) from content that is ultimately derived from attacker-influenceable repository data (conflicting file contents, commit messages) summarized by an LLM.

### Finding Description
The dialog renders per-file reasoning as a raw string: [1](#0-0) 

and the model's markdown summary through `SandboxedMarkdown`, which sanitizes HTML/markdown but not bidi/control characters: [2](#0-1) 

This is structurally the same class of bug as the starknet-snap report: text that is supposed to accurately represent a security-relevant decision (which contract call data will be signed / which side of a conflict will be committed) is shown to the user via string interpolation without neutralizing characters that can visually misrepresent the underlying content. Desktop is aware of this exact class of attack for diffs — it has a dedicated `hasHiddenBidiChars` warning banner: [3](#0-2) 

but that warning is scoped to the diff viewer only, and I found no equivalent check wired into the Copilot conflict-resolution summary/reasoning rendering path.

### Impact Explanation
If the reasoning/summary text (sourced from repository content an attacker controls, such as a malicious file with conflicting hunks, and then summarized by an LLM without stripping control characters) contains bidi override or invisible characters, the on-screen description of "which side was kept" or "why" could be rendered in a way that contradicts the actual resolution the user is about to commit by clicking Continue. Because this dialog gates a repository-modifying action (finishing a merge/rebase/cherry-pick), a misleading display could cause a user to silently commit/push attacker-favored conflict resolutions they did not intend to approve.

### Likelihood Explanation
This requires: (1) the user has the Copilot conflict-resolution feature enabled and configured with a model, (2) the user is resolving conflicts against a branch/commit whose content was crafted by an attacker (e.g., a malicious PR branch, or content merged from a compromised remote), and (3) the LLM's reasoning/summary output preserves attacker-injected control characters verbatim. I could not confirm from the code I reviewed whether the LLM-facing pipeline (`copilot-store.ts`, `copilot-conflict-resolution.ts`) already filters such characters before they reach the UI — this is the main open question limiting confidence in likelihood.

### Recommendation
- Strip or visually neutralize ASCII control characters and Unicode bidi-control characters from `reasoning` and `markdown` fields before rendering them in `CopilotConflictsDialog` / `CopilotConflictsResolutionSummary`, similar to the existing `hasHiddenBidiChars` detection used for diffs.
- Consider wrapping AI-generated reasoning text in a fixed-direction (`dir="ltr"`) container with `unicode-bidi: isolate` to prevent bidi characters from reordering surrounding UI text.
- Verify, and if necessary add, sanitization at the point where the LLM's response is parsed in `app/src/lib/copilot-conflict-resolution.ts` / `app/src/lib/stores/copilot-store.ts` rather than only at render time.

### Proof of Concept
I could not construct or run an end-to-end PoC within this session — it would require configuring the Copilot conflict-resolution feature with a live model and crafting a conflicting file whose content induces the model to reproduce bidi-override characters in its reasoning/summary output, then observing whether the dialog text is rendered misleadingly. This is presented as a code-pattern finding based on the render paths cited above, not a confirmed exploit.

### Citations

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L418-447)
```typescript
    let reasoningText: string | undefined
    if (choice === 'copilot' && reasoning) {
      reasoningText = reasoning
    } else if (isDeleteConflict) {
      const deletedSide = isManualConflict(fileStatus!)
        ? getDeletedSide(fileStatus!)
        : undefined
      const { ourBranch, theirBranch } = this.props.conflictState
      if (deletedSide === 'ours') {
        const branch = ourBranch ?? 'current branch'
        reasoningText =
          choice === 'ours'
            ? `Deleting file (deleted on ${branch})`
            : `Keeping modified file`
      } else if (deletedSide === 'theirs') {
        const branch = theirBranch ?? 'incoming branch'
        reasoningText =
          choice === 'theirs'
            ? `Deleting file (deleted on ${branch})`
            : `Keeping modified file`
      }
    } else if (choice === 'ours') {
      reasoningText = `Using changes from ${
        this.props.conflictState.ourBranch ?? 'current branch'
      }`
    } else if (choice === 'theirs') {
      reasoningText = `Using changes from ${
        this.props.conflictState.theirBranch ?? 'incoming branch'
      }`
    }
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L127-140)
```typescript
  public renderMarkdown = async () => {
    const { markdown } = this.props

    const body = DOMPurify.sanitize(
      marked(markdown, {
        // https://marked.js.org/using_advanced  If true, use approved GitHub
        // Flavored Markdown (GFM) specification.
        gfm: true,
        // https://marked.js.org/using_advanced, If true, add <br> on a single
        // line break (copies GitHub behavior on comments, but not on rendered
        // markdown files). Requires gfm be true.
        breaks: true,
      })
    )
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-53)
```typescript
  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }
```
