### Title
Argv-injection into `git push` via unsanitized `remote.name` (missing `--` separator) - (File: `app/src/lib/git/push.ts`)

### Summary
`push()` builds the `git push` argument vector by directly interpolating `remote.name` as a positional argument, with no `--` separator to stop git's option parser and no validation that the name doesn't begin with `-`/`--`. A remote name is not free-form user typed text under Desktop's control in all cases — it is derived verbatim from `git remote -v` output [1](#0-0) , which in turn reflects the literal `[remote "<name>"]` section header of the repository's `.git/config`. If a user opens/adds a local repository whose `.git/config` was crafted to contain a remote section name such as `--upload-pack=...`, that string flows unchanged into `push.ts`'s `args` array.

### Finding Description
`push()` constructs:
```ts
const args = [
  'push',
  remote.name,
  remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
]
``` [2](#0-1) 

`remote.name` is not validated anywhere along the path: `getRemotes()` parses it straight out of `git remote -v` stdout with a regex and returns it as `IRemote.name` [3](#0-2) , `IRemote` places no constraint on the `name` field [4](#0-3) , and `git-store.ts`/`app-store.ts` pass the loaded remote straight through to `pushRepo` without sanitization [5](#0-4) .

Notably, the codebase is otherwise aware of this exact class of bug: `checkout.ts` appends a trailing `'--'` after the branch name specifically to stop git's option parser before any attacker/branch-controlled string could be read as a flag [6](#0-5) . `push.ts` has no equivalent `--` separator or leading-dash rejection for `remote.name`.

### Impact Explanation
If `git push` parses a positional argument beginning with `-`/`--` as an option, a remote literally named `--upload-pack=/bin/sh` (or similar) could smuggle a flag into the invoked git process, potentially achieving command execution via the local git binary or corrupting the push destination/refspec entirely — a silent-corruption-of-what-the-user-pushes scenario at minimum, and worse if `--upload-pack`/`--exec`-style flags are honored by `git push`'s argument parser.

### Likelihood Explanation
I could not fully verify, without a live git binary, whether `git push`'s option parser (`parse-options.c` with git push's specific flags/`PARSE_OPT_STOP_AT_NON_OPTION` semantics) actually treats a positional `<repository>` argument starting with `--upload-pack=...` as a recognized flag rather than an unrecognized-option error or a literal remote name lookup failure. This is the crux of the reported bug and needs empirical confirmation (the PoC suggested by the reporter).

Separately, the "attacker-controlled cloned repository" premise in the question is weaker than it sounds: a normal `git clone <url>` does not copy the source repository's `.git/config` verbatim — Desktop/git generates a fresh config from the clone URL the user supplied, so a hostile remote-triggered clone alone cannot inject an arbitrary `[remote "..."]` section name. The more plausible vector is a user manually adding/opening a repository whose `.git` directory was pre-crafted (e.g., a downloaded archive containing `.git/config`), which falls closer to "local content the user opened" than "remote/API response" and may fall outside strict scope depending on how "cloned/fetched repository" is interpreted.

### Recommendation
- Reject remote names that begin with `-` when reading/using them (defense in depth alongside git's own `git remote add` name validation, which this data path bypasses because it reads names from config/`git remote -v` output rather than from user-typed `add remote` input).
- Insert a `--` separator before the refspec/positional arguments in `push.ts` (and check `pull.ts`/`fetch.ts` for the same pattern), mirroring the mitigation already used in `checkout.ts` [6](#0-5) .

### Proof of Concept
1. Create/obtain a local git repository whose `.git/config` contains:
   ```
   [remote "--upload-pack=/bin/sh"]
       url = https://example.com/some/repo.git
   ```
2. Open this repository as a local repository in GitHub Desktop (or otherwise get Desktop to call `getRemotes`/`loadRemotes` against it) so `remote.name` becomes the literal string `--upload-pack=/bin/sh` [1](#0-0) .
3. Trigger a push through the UI, which calls `push()` in `push.ts`, producing `args = ['push', '--upload-pack=/bin/sh', '<branch>']` [2](#0-1) .
4. Observe whether the spawned `git push` process honors the injected flag (would need to be confirmed against the actual `dugite`/git binary bundled with Desktop).

Because step 4's actual git argument-parsing behavior was not confirmed in this review, I present this as a plausible but unconfirmed finding rather than a fully validated exploit chain.

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

**File:** app/src/lib/git/push.ts (L57-61)
```typescript
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/models/remote.ts (L12-16)
```typescript
/** A remote as defined in Git. */
export interface IRemote {
  readonly name: string
  readonly url: string
}
```

**File:** app/src/lib/stores/git-store.ts (L1289-1306)
```typescript
  public async loadRemotes(): Promise<void> {
    const remotes = await getRemotes(this.repository)
    this._remotes = remotes
    this._defaultRemote = findDefaultRemote(remotes)

    const currentRemoteName =
      this.tip.kind === TipState.Valid &&
      this.tip.branch.upstreamRemoteName !== null
        ? this.tip.branch.upstreamRemoteName
        : null

    // Load the remote that the current branch is tracking. If the branch
    // is not tracking any remote or the remote which it's tracking has
    // been removed we'll default to the default branch.
    this._currentRemote =
      currentRemoteName !== null
        ? remotes.find(r => r.name === currentRemoteName) || this._defaultRemote
        : this._defaultRemote
```

**File:** app/src/lib/git/checkout.ts (L28-36)
```typescript
async function getBranchCheckoutArgs(branch: Branch) {
  return [
    branch.name,
    ...(branch.type === BranchType.Remote
      ? ['-b', branch.nameWithoutRemote]
      : []),
    '--',
  ]
}
```
