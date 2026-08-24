[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/lib/custom-integration.ts (L80-97)
```typescript
export function expandTargetPathArgument(
  args: ReadonlyArray<string>,
  repoPath: string
): ReadonlyArray<string> {
  // Only strip quotes when the entire argument is the quoted placeholder.
  // Otherwise preserve any user-provided quoting and replace the placeholder
  // in place.
  return args.map(arg => {
    if (
      arg === `'${TargetPathArgument}'` ||
      arg === `"${TargetPathArgument}"`
    ) {
      return repoPath
    }

    return arg.replaceAll(TargetPathArgument, repoPath)
  })
}
```

**File:** app/src/lib/shells/darwin.ts (L209-219)
```typescript
export function launchCustomShell(
  customShell: ICustomIntegration,
  path: string
): ChildProcess {
  const argv = parseCustomIntegrationArguments(customShell.arguments)
  const args = expandTargetPathArgument(argv, path)

  return customShell.bundleID
    ? spawnCustomIntegration('open', ['-b', customShell.bundleID, ...args])
    : spawnCustomIntegration(customShell.path, args)
}
```

**File:** app/src/lib/editors/launch.ts (L34-36)
```typescript
    const child = spawnAsDarwinApp
      ? spawn('open', ['-a', editorPath, ...args], opts)
      : spawn(editorPath, args, opts)
```

**File:** app/src/lib/editors/launch.ts (L70-86)
```typescript
export const launchCustomExternalEditor = (
  fullPath: string,
  customEditor: ICustomIntegration
) => {
  const argv = parseCustomIntegrationArguments(customEditor.arguments)

  // Replace instances of RepoPathArgument with fullPath in customEditor.arguments
  const args = expandTargetPathArgument(argv, fullPath)

  // In macOS we can use `open` if it's an app (i.e. if we have a bundleID),
  // which will open the right executable file for us, we only need the path
  // to the editor .app folder.
  const spawnAsDarwinApp = __DARWIN__ && customEditor.bundleID !== undefined
  const editorName = `custom editor at path '${customEditor.path}'`

  return launchEditor(customEditor.path, args, editorName, spawnAsDarwinApp)
}
```
