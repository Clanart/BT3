[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/git/stash.ts (L283-297)
```typescript
  const args = [
    'stash',
    'show',
    stashSha,
    '--raw',
    '--numstat',
    '-z',
    '--format=format:',
    '--no-show-signature',
    '--',
  ]

  const { stdout } = await git(args, repository.path, 'getStashedFiles')

  return parseRawLogWithNumstat(stdout, stashSha, `${stashSha}^`).files
```

**File:** app/src/lib/git/log.ts (L276-317)
```typescript
export function parseRawLogWithNumstat(
  stdout: string,
  sha: string,
  parentCommitish: string
) {
  const files = new Array<CommittedFileChange>()
  let linesAdded = 0
  let linesDeleted = 0
  let numStatCount = 0
  const lines = stdout.split('\0')

  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i]
    if (line.startsWith(':')) {
      const lineComponents = line.split(' ')
      const srcMode = forceUnwrap(
        'Invalid log output (srcMode)',
        lineComponents[0]?.replace(':', '')
      )
      const dstMode = forceUnwrap(
        'Invalid log output (dstMode)',
        lineComponents[1]
      )
      const status = forceUnwrap(
        'Invalid log output (status)',
        lineComponents.at(-1)
      )
      const oldPath = /^R|C/.test(status)
        ? forceUnwrap('Missing old path', lines.at(++i))
        : undefined

      const path = forceUnwrap('Missing path', lines.at(++i))

      files.push(
        new CommittedFileChange(
          path,
          mapStatus(status, oldPath, srcMode, dstMode),
          sha,
          parentCommitish
        )
      )
    } else {
```
