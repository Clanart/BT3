[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/lib/git/spawn.ts (L21-38)
```typescript
export const spawnGit = (
  args: string[],
  path: string,
  name: string,
  options?: SpawnOptions
) =>
  withTrampolineEnv(
    trampolineEnv =>
      GitPerf.measure(`${name}: git ${args.join(' ')}`, async () =>
        spawn(args, path, {
          ...options,
          env: { ...options?.env, ...trampolineEnv },
        })
      ),
    path,
    options?.isBackgroundTask ?? false,
    options?.env
  )
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L101-103)
```typescript
  return withTrampolineToken(async token => {
    isBackgroundTaskEnvironment.set(token, isBackgroundTask)
    trampolineEnvironmentPath.set(token, path)
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L195-200)
```typescript
    } finally {
      removeMostRecentSSHCredential(token)
      isBackgroundTaskEnvironment.delete(token)
      hasRejectedCredentialsForEndpoint.delete(token)
      trampolineEnvironmentPath.delete(token)
    }
```

**File:** app/src/lib/git/fetch.ts (L39-56)
```typescript
export async function fetch(
  repository: Repository,
  remote: IRemote,
  progressCallback?: (progress: IFetchProgress) => void,
  isBackgroundTask = false
): Promise<void> {
  let opts: IGitStringExecutionOptions = {
    successExitCodes: new Set([0]),
    env: await envForRemoteOperation(remote.url),
  }

  if (progressCallback) {
    const title = `Fetching ${remote.name}`
    const kind = 'fetch'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true, isBackgroundTask },
      new FetchProgressParser(),
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L28-37)
```typescript
  // We'll accept github.com as valid host automatically. GitHub's public key
  // fingerprint can be obtained from
  // https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
  if (
    info.host === 'github.com' &&
    info.keyType === 'RSA' &&
    info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'
  ) {
    return 'yes'
  }
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L39-44)
```typescript
  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHHostAuthenticity: background task environment, skipping prompt'
    )
    return undefined
  }
```

**File:** app/src/lib/trampoline/trampoline-command.ts (L40-49)
```typescript
/**
 * Represents a handler function for a trampoline command.
 *
 * @param   command The invoked trampoline command to handle.
 * @returns         A string with the result of the command (which will be
 * printed via
 *          stdout by the trampoline client), or undefined
 */
export type TrampolineCommandHandler = (
  command: ITrampolineCommand
```
