## Title
Argument Injection via Unsanitized `remote.name` in `git push` — (File: `app/src/lib/git/push.ts`)

## Summary
`remote.name` is read verbatim from `git remote -v` output (which reflects whatever a `.git/config` `[remote "…"]` section header contains) and is inserted as a bare positional argument to `git push` with no leading `--` separator and no character validation. A remote name beginning with `-` (e.g. `--upload-pack=...` / `--receive-pack=/path/to/payload`) is therefore parsed by `git` as an option rather than as the remote nickname/URL, allowing an attacker who controls a repository's `.git/config` to influence the argv passed to the `git push` child process.

## Finding Description
`getRemotes()` extracts the remote name straight from `git remote -v` stdout using a permissive regex that accepts any character sequence up to a tab: [1](#0-0) 

That value is stored, unaltered, as `IRemote.name` and flows into `push()`, where it is placed as `args[1]` right after the `push` subcommand, with no `--` end-of-options marker preceding it: [2](#0-1) 

The same unguarded pattern (`[<subcommand>, remote.name, ...]`, no `--` separator) is repeated in several other sibling git wrappers in the same directory, indicating the omission is systemic rather than a one-off: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

By contrast, other command wrappers in `app/src/lib/git/` (e.g. `checkout.ts`, `diff.ts`, `reset.ts`) do use a `'--'` separator before user-controlled path/ref arguments, confirming that the maintainers are aware of and normally mitigate this class of argument-injection issue, but did not apply it consistently to `remote.name`. There is no dedicated validator restricting remote names to safe characters comparable to `sanitizedRefName`/`testForInvalidChars`, which is only applied to branch/ref names: [7](#0-6) 

Because `.git/config` is a plain text file and git's own config-section-name quoting rules are far more permissive than `git remote add`'s CLI validation, a repository can be crafted (e.g. distributed as a folder/archive and opened via "Add Local Repository," or otherwise arriving with a pre-populated `.git/config`) with a `[remote "--receive-pack=/malicious/executable"]` section. When GitHub Desktop subsequently calls `getRemotes()` and then `push()` against that remote object, the resulting argv is:
```
git push --receive-pack=/malicious/executable <branch-refspec>
```
which `git` parses as an option, not a remote nickname.

## Impact Explanation
`--receive-pack=<path>` (or its `--upload-pack=` fetch/pull analogues) instructs `git` to invoke the given string as the remote-side executable when the effective transport is local, `file://`, `ext::`, or (in some configurations) `ssh`. This can be leveraged to have `git` execute an attacker-chosen program/command during a normal push/pull/fetch operation initiated entirely inside GitHub Desktop — i.e., code execution triggered merely by opening an attacker-crafted repository and performing an ordinary push, without any unusual user action beyond that.

## Likelihood Explanation
Exploitation requires the victim to open/import a repository whose `.git/config` already contains the malicious remote section and then push (or fetch/pull) using that remote — a plausible workflow for "Add Local Repository" or repositories obtained via non-`git clone` means (zips, shared folders, etc.). It does not require a true `git clone`, since `git clone` always regenerates `.git/config` locally with a sane `origin` entry; the premise depends on the attacker controlling the `.git/config` file content directly. This narrows but does not eliminate the practical attack surface within the described scope (attacker-controlled repository content).

## Recommendation
- Insert a `'--'` end-of-options separator before `remote.name` (and any other positional remote/ref argument) in `push.ts`, `fetch.ts`, `pull.ts`, `tag.ts`, and `branch.ts`, consistent with how `checkout.ts`/`diff.ts`/`reset.ts` already do.
- Additionally validate/reject remote names that start with `-` when they are read back from `getRemotes()` (or refuse to operate on such remotes), rather than relying solely on argv-position hardening.

## Proof of Concept
1. Create a directory with a `.git` folder whose `config` contains:
```
[remote "--receive-pack=/tmp/evil.sh"]
    url = /path/to/some/valid/local/repo
    fetch = +refs/heads/*:refs/remotes/--receive-pack=/tmp/evil.sh/*
```
2. Open this folder in GitHub Desktop via "Add Local Repository."
3. Trigger a push against that remote (e.g. via the UI or `dispatcher.push`), which calls `push()` in [2](#0-1)  with `remote.name === '--receive-pack=/tmp/evil.sh'`.
4. Observe that the spawned `git push` process receives `--receive-pack=/tmp/evil.sh` as an option rather than a remote identifier, causing `/tmp/evil.sh` to be invoked as the remote-side executable when the local/`ext`/`ssh` transport path is taken.

### Citations

**File:** app/src/lib/git/remote.ts (L15-25)
```typescript
  const result = await git(['remote', '-v'], repository.path, 'getRemotes', {
    expectedErrors: new Set([GitError.NotAGitRepository]),
  })

  if (result.gitError === GitError.NotAGitRepository) {
    return []
  }

  return [...result.stdout.matchAll(/^(.+)\t(.+)\s\(fetch\)/gm)].map(
    ([, name, url]) => ({ name, url })
  )
```

**File:** app/src/lib/git/push.ts (L57-61)
```typescript
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/lib/git/fetch.ts (L96-101)
```typescript
): Promise<void> {
  await git(['fetch', remote.name, refspec], repository.path, 'fetchRefspec', {
    successExitCodes: new Set([0, 128]),
    env: await envForRemoteOperation(remote.url),
  })
}
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

**File:** app/src/lib/git/tag.ts (L91-99)
```typescript
  const args = [
    'push',
    remote.name,
    branchName,
    '--follow-tags',
    '--dry-run',
    '--no-verify',
    '--porcelain',
  ]
```

**File:** app/src/lib/git/branch.ts (L115-120)
```typescript
export async function deleteRemoteBranch(
  repository: Repository,
  remote: IRemote,
  remoteBranchName: string
): Promise<true> {
  const args = ['push', remote.name, `:${remoteBranchName}`]
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-16)
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

/** Validate that a reference does not contain any invalid characters */
export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```
