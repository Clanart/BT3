[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/text-token-parser.ts (L68-74)
```typescript
  public constructor(emoji: Map<string, Emoji>, repository?: Repository) {
    this.allEmoji = emoji

    if (repository && isRepositoryWithGitHubRepository(repository)) {
      this.repository = getNonForkGitHubRepository(repository)
    }
  }
```

**File:** app/src/lib/text-token-parser.ts (L176-178)
```typescript
    const url = `${repository.htmlURL}/issues/${id}`
    this._results.push({ kind: TokenType.Link, text: maybeIssue, url })
    return { nextIndex }
```

**File:** app/src/lib/text-token-parser.ts (L207-209)
```typescript
    const name = maybeMention.substring(1)
    const url = `${getHTMLURL(repository.endpoint)}/${name}`
    this._results.push({ kind: TokenType.Link, text: maybeMention, url })
```

**File:** app/src/models/repository.ts (L189-210)
```typescript
export function getNonForkGitHubRepository(
  repository: RepositoryWithGitHubRepository
): GitHubRepository {
  if (!isRepositoryWithForkedGitHubRepository(repository)) {
    // If the repository is not a fork, we don't have to worry about anything.
    return repository.gitHubRepository
  }

  const forkContributionTarget = getForkContributionTarget(repository)

  switch (forkContributionTarget) {
    case ForkContributionTarget.Self:
      return repository.gitHubRepository
    case ForkContributionTarget.Parent:
      return repository.gitHubRepository.parent
    default:
      return assertNever(
        forkContributionTarget,
        'Invalid fork contribution target'
      )
  }
}
```

**File:** app/src/ui/lib/link-button.tsx (L76-92)
```typescript
  private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()

    if (this.props.disabled) {
      return
    }

    const uri = this.props.uri
    if (uri) {
      shell.openExternal(uri)
    }

    const onClick = this.props.onClick
    if (onClick) {
      onClick()
    }
  }
```

**File:** app/src/lib/app-shell.ts (L43-53)
```typescript
export const shell: IAppShell = {
  // Since Electron 13, shell.trashItem doesn't work from the renderer process
  // on Windows. Therefore, we must invoke it from the main process. See
  // https://github.com/electron/electron/issues/29598
  moveItemToTrash,
  beep: electronShell.beep,
  openExternal,
  showItemInFolder,
  showFolderContents,
  openPath: electronShell.openPath,
}
```
