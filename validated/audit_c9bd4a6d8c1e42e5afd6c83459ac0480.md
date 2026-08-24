### Title
Argument Injection via Malicious Remote Name in `git fetch` (missing `--` separator) - ([File: app/src/lib/git/fetch.ts])

### Summary
`getFetchArgs` builds the `git fetch` argv as `['fetch', ..., '--prune', '--recurse-submodules=on-demand', remote]`, passing the remote name as a trailing positional argument with no `--` separator before it. [1](#0-0) 
The `remote` value originates from `remote.name`, which is populated directly from parsing `git remote -v` output — i.e., whatever is defined under `[remote "<name>"]` sections in the repository's `.git/config` — with no validation or sanitization of the name. [2](#0-1) 

### Finding Description
`git`'s argument parser (`parse-options`) treats any token beginning with `-`/`--` as an option regardless of its position in argv, unless a `--` separator has already been consumed. Because GitHub Desktop never inserts a `--` before the remote-name argument in `fetch()`, `push()`, or `pull()`, a remote whose *name* (not URL) starts with `-` is parsed as an option rather than a positional remote/repository argument. [3](#0-2) [4](#0-3) [5](#0-4) 

Notably, this exact class of bug was already recognized and mitigated elsewhere in this same file family: `clone.ts` explicitly inserts a `--` separator before the positional `url`/`path` arguments and even added a dedicated `isClonePathSensitive` guard as "a backstop against path traversal attacks where a crafted URL tricks the UI." [6](#0-5) 
That same hardening pattern was not applied to `fetch.ts` (or `push.ts`/`pull.ts`), leaving the remote-name argument unguarded.

`getRemotes` has no restriction on the remote name's character set — it only requires the `git remote -v` output line to match `^(.+)\t(.+)\s\(fetch\)`, which happily captures names starting with `-`. [7](#0-6) 
Compare this to `sanitizedRefName`, used elsewhere in the codebase, which explicitly strips leading `-`/`+` characters from names before they're used as git refs — showing the project is aware leading-dash names are dangerous, but that sanitizer is not applied to remote names used here. [8](#0-7) 

While `addRemote` (used by GitHub Desktop's own UI/flows to add a remote) passes the name straight to `git remote add name url` without validation, [9](#0-8) 
git itself may reject overtly malformed names via `remote add`. However, `.git/config` is a plain text file; if it is crafted directly (e.g., shipped as part of a distributed repository folder/archive that a victim adds via "Add Local Repository", or via any repository-content vector in scope) rather than produced through `git remote add`, git's config parser will still expose the malicious subsection name (`[remote "--upload-pack=..."]`) via `git remote -v`, and `getRemotes` will faithfully report it as `remote.name`.

Once `remote.name` is `--upload-pack=<command>`, `getFetchArgs` produces `['fetch', '--prune', '--recurse-submodules=on-demand', '--upload-pack=<command>']`. `git fetch`'s documented `--upload-pack=<upload-pack>` option lets the caller specify an arbitrary executable path to be invoked as the "upload-pack" program on the transport side (relevant for local/`file://`/`ext::`-style or ssh transports), which is the classic git argument-injection RCE primitive.

### Impact Explanation
If exploitable, this would let a repository the user opens or fetches trigger execution of an attacker-chosen program merely by the user performing a normal, expected action ("Fetch origin"), satisfying the "code execution" impact bar for repository-content-controlled attacks. It is also inconsistent hardening: the same author(s) fixed an analogous class of bug in `clone.ts` but left `fetch.ts`/`push.ts`/`pull.ts` unguarded.

### Likelihood Explanation
Exploitability hinges on facts I could **not fully verify from the code alone** with the tools available:
- Whether `git`'s config parser accepts and round-trips a subsection name containing characters like `=`, `;`, `$`, spaces (git config subsection names support arbitrary strings including such characters when properly quoted in the file, per git's config file format), and whether `git remote -v`'s single-line, tab-delimited output format would still allow `getRemotes`'s regex to correctly parse a name containing embedded newlines/tabs (the PoC's exact payload `--upload-pack=touch${IFS}pwned;` contains no tab/newline, so it should parse fine).
- Whether `--upload-pack=<cmd>` is actually honored for the transport used against the local default remote URL (this option is meaningful for `ssh`/`ext`/local transports, not plain `https://` remotes) — i.e., the malicious `.git/config` would also need to pair this remote name with a URL/transport where `--upload-pack` is actually invoked (e.g. a local path or `ssh://` URL), which is achievable since the URL is also attacker-controlled in the same config file.
- Whether GitHub Desktop's actual invocation path for `fetch()` in `app-store.ts`/`git-store.ts` ever validates or filters remote names before calling `fetch()` — I found no such validation in the reviewed call sites, but did not exhaustively trace every UI entry point that triggers a background/user fetch.

Given the missing `--` separator and the absence of any name validation in `getRemotes`, this is a real code-level gap; the main uncertainty is around the precise mechanics of triggering `--upload-pack` command execution against a specific transport (this typically requires the remote URL to be `ssh://`, a local path, or similar rather than `https://`, but the URL is equally attacker-controlled here).

### Recommendation
Insert a `--` separator before the trailing `remote` positional argument in `getFetchArgs` (and the equivalent positional remote-name arguments in `push.ts` and `pull.ts`), mirroring the pattern already used in `clone.ts`:
```ts
return [
  'fetch',
  ...(progressCallback ? ['--progress'] : []),
  '--prune',
  '--recurse-submodules=on-demand',
  '--',
  remote,
]
```
Additionally, consider validating/sanitizing remote names sourced from `getRemotes` (reject or escape names beginning with `-`), analogous to `sanitizedRefName`.

### Proof of Concept
Not independently verified end-to-end due to tool limitations (no shell/execution access), but the code-level path is:
1. Craft a `.git/config` containing:
   ```
   [remote "--upload-pack=touch${IFS}pwned;"]
       url = ssh://127.0.0.1/whatever
       fetch = +refs/heads/*:refs/remotes/origin/*
   ```
2. Have the victim open this directory as a local repository in GitHub Desktop (File > Add Local Repository), or ship it as a directory that gets opened by any repository-content vector in scope.
3. Trigger a fetch (the "Fetch origin" button, or GitHub Desktop's periodic background fetch).
4. `getRemotes` returns `{ name: "--upload-pack=touch${IFS}pwned;", url: "ssh://127.0.0.1/whatever" }`. [7](#0-6) 
5. `fetch()` calls `getFetchArgs(remote.name, ...)`, producing `['fetch','--prune','--recurse-submodules=on-demand','--upload-pack=touch${IFS}pwned;']` with no leading URL/remote and no `--` separator. [1](#0-0) 
6. `git`, parsing `--upload-pack=...` as an option, invokes the specified program as the upload-pack executable for the transport, executing the injected command.

I was not able to confirm (without a live git/dugite execution environment) that step 6 succeeds for this exact URL scheme/transport combination, so this PoC needs to be validated end-to-end by a security engineer with an execution environment before being treated as fully confirmed.

### Citations

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

**File:** app/src/lib/git/fetch.ts (L86-88)
```typescript
  const args = await getFetchArgs(remote.name, progressCallback)

  await git(args, repository.path, 'fetch', opts)
```

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

**File:** app/src/lib/git/remote.ts (L29-37)
```typescript
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
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

**File:** app/src/lib/git/pull.ts (L96-104)
```typescript
  const args = [
    ...gitRebaseArguments(),
    'pull',
    ...(await getDefaultPullDivergentBranchArguments(repository)),
    '--recurse-submodules',
    ...(options?.progressCallback ? ['--progress'] : []),
    ...(options?.noVerify ? ['--no-verify'] : []),
    remote.name,
  ]
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-11)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```
