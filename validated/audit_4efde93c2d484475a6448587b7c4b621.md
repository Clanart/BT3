No remote-name validation exists anywhere in the traced path. `getRemotes` in `app/src/lib/git/remote.ts:12-26` simply parses the raw output of `git remote -v` with a regex and returns whatever name appears in `.git/config`, with no character/format check: [1](#0-0) 

That `IRemote.name` is passed unmodified all the way through `GitStore.fetch` → `fetchRemotes` → `fetchRemote` → `fetchRepo`, ending at `getFetchArgs`, which appends it as the last positional argument to `git fetch`: [2](#0-1) [3](#0-2) 

Nothing in `core.ts`'s `git()` wrapper inserts a `--` separator before the remote argument, and no repo/git-store code validates that a remote name isn't option-like (e.g. starts with `-`). I searched for any such validation (`isValidRemoteName`, dash-prefix checks, `--end-of-options`) and found none in the git/remote/fetch code paths — the only hits were unrelated UI sanitizers for branch/ref names and clone paths, none of which apply to remote *names* read from an existing repo's config.

Whether this is *exploitable end-to-end* through the specific indicator-updater path depends on one fact I could not fully confirm from the index: whether `git`/dugite's config parser accepts a section header like `[remote "--upload-pack=..."]` and whether `git remote -v` actually emits that string as a remote name (git's `remote add` CLI validates names, but hand-edited/cloned `.git/config` content bypasses that check at the config-parsing layer — this is consistent with historical git/Desktop argument-injection issues, but I did not find a Desktop-specific test or comment in this index confirming this exact behavior was reproduced). I could not locate any code in this repo that would block it, so on the evidence available, the injection path is real and unmitigated by Desktop code.

### Title
Argument injection via option-like remote name reaching `git fetch` - (`app/src/lib/git/fetch.ts`)

### Summary
`RepositoryIndicatorUpdater` periodically calls `fetchForRepositoryIndicator` → `gitStore.fetch` → `fetchRemotes` → `fetchRemote` → `fetchRepo` for every open repository, using remote objects obtained via `getRemotes`, which trusts whatever name string appears in `.git/config`. `getFetchArgs` (`app/src/lib/git/fetch.ts:9-20`) appends that untrusted string as the final, unguarded positional argument to `git fetch`.

### Finding Description
`getRemotes` (`app/src/lib/git/remote.ts:12-26`) extracts remote names purely from the textual output of `git remote -v` with no format/character validation. If a repository's `.git/config` contains a remote section whose name is an option-like string (e.g. `--upload-pack=...`), that string flows unchanged as `remote.name` through `GitStore.fetch`/`fetchRemotes`/`fetchRemote`/`fetchRepo` into `getFetchArgs`, which places it as the trailing argument of `['fetch', ..., remote]` with no leading `--` separator to force positional interpretation. Git fetch will parse a leading-dash token as an option rather than a remote/refspec.

### Impact Explanation
If exploitable, `--upload-pack=<command>` (or similar options such as `--exec`) causes git to execute an attacker-chosen command in place of the normal `upload-pack` invocation whenever the local transport is used, giving arbitrary command execution under the user's account outside any repo-content sandbox.

### Likelihood Explanation
The indicator updater runs automatically and periodically in the background for every listed repository without user interaction, so if such a config can be introduced into a repository directory a user opens in Desktop, exploitation would be silent and automatic. However, I could not verify from the codebase alone that git's config parser/`remote add` machinery permits creating such a malicious section name via normal clone operations (vs. requiring direct filesystem placement of a crafted `.git/config`), which affects whether this qualifies under the "cloned/fetched repository" threat model versus a local-file-tampering scenario that would fall outside scope.

### Recommendation
Validate remote names before use (reject or reject names starting with `-`), and/or always pass an explicit `--` separator before the remote name in `getFetchArgs` and similar call sites (`fetchRefspec`, `updateRemoteHEAD`, etc.) to force git to treat the following token as a positional argument rather than an option.

### Proof of Concept
Not independently verified in this review — would require constructing a test repository whose `.git/config` contains `[remote "--upload-pack=touch /tmp/pwned;"]` with a `url`/`fetch` entry, confirming `git remote -v` surfaces that literal name, then asserting that `git()` is invoked by `fetchRepo`/`getFetchArgs` with that string as the trailing option-like argument (as suggested in the original question), e.g. spying on `git()` in `app/src/lib/git/core.ts` and calling `fetch()` from `app/src/lib/git/fetch.ts` against such a fixture.

### Citations

**File:** app/src/lib/git/remote.ts (L12-26)
```typescript
export async function getRemotes(
  repository: Repository
): Promise<ReadonlyArray<IRemote>> {
  const result = await git(['remote', '-v'], repository.path, 'getRemotes', {
    expectedErrors: new Set([GitError.NotAGitRepository]),
  })

  if (result.gitError === GitError.NotAGitRepository) {
    return []
  }

  return [...result.stdout.matchAll(/^(.+)\t(.+)\s\(fetch\)/gm)].map(
    ([, name, url]) => ({ name, url })
  )
}
```

**File:** app/src/lib/git/fetch.ts (L9-20)
```typescript
async function getFetchArgs(
  remote: string,
  progressCallback?: (progress: IFetchProgress) => void
) {
  return [
    'fetch',
    ...(progressCallback ? ['--progress'] : []),
    '--prune',
    '--recurse-submodules=on-demand',
    remote,
  ]
}
```

**File:** app/src/lib/stores/git-store.ts (L1077-1089)
```typescript
  public async fetchRemote(
    remote: IRemote,
    backgroundTask: boolean,
    progressCallback?: (fetchProgress: IFetchProgress) => void
  ): Promise<void> {
    const repo = this.repository
    const retryAction: RetryAction = {
      type: RetryActionType.Fetch,
      repository: repo,
    }
    const fetchSucceeded = await this.performFailableOperation(
      async () => {
        await fetchRepo(repo, remote, progressCallback, backgroundTask)
```
