### Title
Malformed `git remote -v` parsing via embedded tab/newline in `.git/config` values leads to misattributed remote name/URL - (File: `app/src/lib/git/remote.ts`)

### Summary
`getRemotes()` parses the raw text output of `git remote -v` with a regular expression that assumes exactly one remote entry per physical line, delimited by a literal tab and a trailing ` (fetch)` marker. Git's config file format allows arbitrary bytes — including literal tab (`\t`) and newline (`\n`) characters — to be embedded in a remote's `name` or `url` value via double-quoted escape sequences (e.g. `url = "http://good.example/x\n evil\thttp://attacker.example/y (fetch)"`). Since `git remote` (`builtin/remote.c`) prints these values verbatim without escaping, a crafted `.git/config` (fully attacker-controlled in a cloned/fetched repository, or merged in via a submodule's config) can make the raw `stdout` deviate from the "one remote per line" invariant the regex depends on, causing the regex to mis-split which bytes belong to `name` vs. `url`.

### Finding Description
`getRemotes()` runs: [1](#0-0) 

The regex `/^(.+)\t(.+)\s\(fetch\)/gm` relies on `.` never matching a newline (no `s` flag) and on each remote's fetch line being fully self-contained between `^`/line boundaries. This is safe only if remote names/URLs never contain raw `\t` or `\n` bytes. However, git's config value grammar supports C-style escapes inside double-quoted strings (`\t`, `\n`, `\\`, `\"`), which are unescaped to literal control-byte values when the config is read — this is a legitimate, documented feature of `git config`, not a bug in git itself. An attacker who controls a repository's `.git/config` (or a submodule's config that gets merged/read) can therefore set, e.g.:

```
[remote "evil"]
    url = "http://good.example.com/legit\n decoy\thttp://attacker.example.com/payload (fetch)"
```

`git remote -v`'s raw output then contains an embedded real newline byte splitting what is logically one remote entry into two *apparent* lines. Because the regex only requires a line ending in ` (fetch)`, it will skip the first fragment (no match) and match the second physical line, capturing `name="decoy"` / `url="http://attacker.example.com/payload (fetch)"` (with the greedy `(.+)` backtracking to swallow the embedded `(fetch)` text into the URL) instead of the real `name="evil"` with its full multi-line URL value.

### Impact Explanation
The mis-parsed `IRemote` values flow into two places:
1. `remote.tsx` renders `remote.name` in the dialog label and `remote.url` in the editable `TextBox`, so a user editing "the remote" is shown data that does not correspond to the actual configured remote. [2](#0-1) 
2. More importantly, `findDefaultRemote()` selects the "origin" remote via an exact string match on `IRemote.name`: [3](#0-2) 
If the mis-parse corrupts the captured `name` for the real `origin` entry (e.g., producing a name with leading/embedded whitespace, or swallowing part of a following line into the name), `x.name === 'origin'` fails and the store falls back to `remotes[0]` — an attacker-influenced ordering/entry. Push operations then run `git push <remote.name> ...` using that mismatched `IRemote`: [4](#0-3) 
This can cause GitHub Desktop to silently push/fetch against a different remote/URL than the one the user believes they are operating on ("origin"), meeting the "silent corruption of what the user commits or pushes" impact criterion, and also spoofs the Remote URL shown in Repository Settings.

### Likelihood Explanation
Exploitation requires only that the victim clone/fetch a repository (or add it as a submodule) whose `.git/config` (or merged submodule config) contains a remote value with an escaped `\t`/`\n` sequence — no elevated privileges, no dependency on credentials, and no unusual user action beyond normal repository usage (opening Repository Settings, or simply pushing). The construction relies on standard, documented git-config quoting/escaping semantics, making it straightforward to reproduce.

### Recommendation
Do not parse the free-text output of `git remote -v` with a line-oriented regex. Instead, enumerate remotes via a NUL-safe/structured mechanism, e.g. iterate `git config --get-regexp --null '^remote\..*\.url$'` (using `-z`) to get exact, unambiguous name/value pairs, or call `git remote` (name list) plus `git remote get-url <name>` per remote. This removes any dependency on delimiter characters that can appear inside the values themselves.

### Proof of Concept
1. Create a test repository and directly edit `.git/config` to add:
```
[remote "evil"]
    url = "http://good.example.com/legit\n decoy\thttp://attacker.example.com/payload (fetch)"
```
2. Run `git remote -v` in that repo and observe the raw stdout contains an embedded literal newline splitting the single logical entry into two physical lines.
3. Run GitHub Desktop's `getRemotes()` (`app/src/lib/git/remote.ts:23-25`) against this repository and observe the returned `IRemote[]` contains `{ name: "decoy", url: "http://attacker.example.com/payload (fetch)" }` instead of the actual configured `{ name: "evil", url: "http://good.example.com/legit\n decoy\thttp://attacker.example.com/payload (fetch)" }`, diverging from the true git configuration.

### Citations

**File:** app/src/lib/git/remote.ts (L15-26)
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
}
```

**File:** app/src/ui/repository-settings/remote.tsx (L16-29)
```typescript
  public render() {
    const remote = this.props.remote
    return (
      <DialogContent>
        <TextBox
          placeholder="Remote URL"
          label={
            __DARWIN__
              ? `Primary Remote Repository (${remote.name}) URL`
              : `Primary remote repository (${remote.name}) URL`
          }
          value={remote.url}
          onValueChanged={this.props.onRemoteUrlChanged}
        />
```

**File:** app/src/lib/stores/helpers/find-default-remote.ts (L12-16)
```typescript
export function findDefaultRemote(
  remotes: ReadonlyArray<IRemote>
): IRemote | null {
  return remotes.find(x => x.name === 'origin') || remotes[0] || null
}
```

**File:** app/src/lib/git/push.ts (L48-60)
```typescript
export async function push(
  repository: Repository,
  remote: IRemote,
  localBranch: string,
  remoteBranch: string | null,
  tagsToPush: ReadonlyArray<string> | null,
  options?: PushOptions,
  progressCallback?: (progress: IPushProgress) => void
): Promise<void> {
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
```
