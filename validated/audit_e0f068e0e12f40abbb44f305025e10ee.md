### Title
Inconsistent Path-Traversal Validation Between Deep-Link File Opening and Commit/PR File Context Menus - ([File: app/src/ui/history/selected-commits.tsx], [File: app/src/ui/open-pull-request/pull-request-files-changed.tsx])

### Summary
GitHub Desktop applies path-traversal protection (`resolveWithin` + an `isAbsolute` rejection) to the `filepath` parameter of the `x-github-client://openRepo` deep link before it is used to open/reveal a file, but the conceptually identical operation — turning a repository-relative file path into an absolute path to reveal in Finder/Explorer or open in an external editor — is performed without this protection when the path comes from commit history or a GitHub API pull-request file list. This mirrors the seed report's core defect: the same logical value (a path that should be constrained to be "inside the repository") is validated in one code path but not in the sibling code path that performs the same action.

### Finding Description
In `app/src/ui/dispatcher/dispatcher.ts`, the deep-link handler for `IOpenRepositoryFromURLAction` explicitly treats `filepath` as untrusted and defends against escaping the repository root: [1](#0-0) 

This shows the team is aware that a repo-relative "file to reveal" value must be resolved with `resolveWithin(repository.path, filepath)` and rejected if it is absolute or escapes the repo, because the value can be attacker-supplied (via a clickable deep link).

However, `file.path` values obtained from a commit's file list or from a GitHub pull request's changed-files list are joined directly onto `repository.path` with plain `Path.join`, with no equivalent `resolveWithin`/`isAbsolute` check, before being used to reveal-in-file-manager, open in an external editor, or write the resolved absolute path to the clipboard: [2](#0-1) [3](#0-2) 

Both `file.path` sources are attacker-influenced under the task's valid-impact criteria: the commit-history menu operates on a cloned/fetched repository's commit data, and the PR files-changed menu operates on a GitHub API object (the PR diff/file list) for a repository the victim did not author. `Path.join` performs lexical normalization only — it does not confirm the final path stays inside `repository.path` — which is exactly why `resolveWithin` exists and is used at the deep-link call site but is absent here.

### Impact Explanation
If a `file.path` value can contain traversal segments (e.g., via unusual encodings, case-folding tricks on case-insensitive filesystems, or malformed diff metadata from the GitHub API that isn't strictly re-validated against the actual git tree), the resolved `fullPath` can point outside the repository. Because this path is then handed to `revealInFileManager`, the external editor launcher, or copied to the clipboard, a victim who opens the context menu on a maliciously named file in a fetched repo or an opened pull request could have Desktop open/reveal a location outside the intended repository — the same class of "read/interact outside the repo" issue that `resolveWithin` was introduced to prevent for deep links.

### Likelihood Explanation
Exploitability depends on whether `file.path` can actually smuggle a traversal segment past git's own tree-entry validation and Desktop's diff parsing — something I could not fully verify given the available context. Git itself normally rejects `..` path components in tree entries, so likelihood is lower than the deep-link case (where the value is a raw, attacker-formed URL query string with no such structural constraint). This is the main uncertainty in this analog: the missing guard is real and demonstrably inconsistent with the deep-link code path, but I do not have direct evidence in this codebase of a way to make `file.path` itself contain `..`/absolute segments end-to-end from a hostile commit or PR payload.

### Recommendation
Apply the same `resolveWithin`/`isAbsolute` validation used in `dispatcher.ts`'s `openRepositoryFromUrl` to every other site that turns a repository-relative `file.path` (from commit data or GitHub API PR data) into an absolute path before revealing it in the file manager, opening it in an external editor, or exposing it via clipboard — i.e., centralize this into a single helper used by `selected-commits.tsx`, `pull-request-files-changed.tsx`, `filter-changes-list.tsx`, and `copilot-conflicts-dialog.tsx`, rather than re-implementing `Path.join(repository.path, file.path)` ad hoc in each UI component.

### Proof of Concept
Not independently reproducible from the indexed code alone: doing so requires confirming that a crafted commit or PR file-list entry can carry a `file.path` value containing traversal or absolute-path content that survives git's tree parsing / Desktop's diff ingestion unmodified, which I was unable to verify within the available tool budget. The concrete, verifiable evidence is the code-level inconsistency itself: `dispatcher.ts` lines 1957-1972 guard the same "resolve path in repo" operation that `selected-commits.tsx` line 384 and `pull-request-files-changed.tsx` line 162 perform without a guard.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/ui/history/selected-commits.tsx (L371-431)
```typescript
  private onContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const {
      selectedCommits,
      localCommitSHAs,
      repository,
      externalEditorLabel,
    } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

    const extension = Path.extname(file.path)

    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor = externalEditorLabel
      ? `Open in ${externalEditorLabel}`
      : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => this.props.onOpenInExternalEditor(file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenItem(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
      { type: 'separator' },
    ]
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-211)
```typescript
  private onFileContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const { repository } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

    const { externalEditorLabel, dispatcher } = this.props

    const extension = Path.extname(file.path)
    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor =
      externalEditorLabel !== undefined
        ? `Open in ${externalEditorLabel}`
        : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => dispatcher.openInExternalEditor(fullPath),
        enabled: fileExistsOnDisk,
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
      { type: 'separator' },
    ]
```
