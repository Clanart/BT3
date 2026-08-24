This is a real, unmitigated argument-injection gap. Confirmed by reading the code:

- `git/clone.ts` uses `args.push('--', url, path)` before invoking git [1](#0-0) , and `repository-scaffolding.ts`'s test helper likewise uses `['clone', repository.path, '--', newDirectory]` [2](#0-1)  — showing the codebase is aware of and defends against exactly this class of git argv-injection for `clone`.
- `addRemote` in `app/src/lib/git/remote.ts` does **not** apply the same `--` separator: it invokes `git(['remote', 'add', name, url], ...)` with no `--` before `url` [3](#0-2) .
- `_publishRepository` in `app-store.ts` passes `apiRepository.clone_url` — a string taken verbatim from the `createRepository` API response — straight into `addRemote` as the URL argument, with no scheme validation or leading-`-` stripping [4](#0-3) .
- `IAPIRepository.clone_url` is a plain string field populated from API/network responses throughout `api-repositories-store.ts` (e.g. `repositories.set(r.clone_url, r)`) with no sanitization applied to the value itself [5](#0-4) .
- Other user-provided-name sanitizers exist in this codebase (`sanitizedRefName`, `sanitizedRepositoryName`, `sanitizeCloneName`), all of which explicitly strip a leading `-`/`+` [6](#0-5)  — but no equivalent guard exists for the URL passed to `addRemote`.

Since `dugite`'s `exec` invokes `git` via `execFile` (no shell), there's no shell-metacharacter risk, but `git remote add [options] <name> <url>` uses `parse_options()` internally, which scans all positional arguments for tokens starting with `-` and treats them as flags unless a `--` terminator is present. A `clone_url` value such as `--upload-pack=touch${IFS}/tmp/pwned;` (or a benign-looking `-o` malformed value) sent back by a malicious/compromised GitHub Enterprise Server (or a MITM'd `createRepository` response) would be parsed as a `git-remote` option rather than a positional URL, potentially altering remote behavior or, depending on the specific flag abused, triggering unintended local file writes to the repo's `.git/config` or other side effects. This matches the "GitHub API object under attacker influence" category in scope (a malicious/compromised API host response), since GitHub Desktop supports arbitrary GitHub Enterprise endpoints whose API responses aren't otherwise integrity-checked beyond TLS.

### Title
Argument-injection via unsanitized `clone_url` in `addRemote` during repository publish - (File: app/src/lib/git/remote.ts)

### Summary
`addRemote()` builds the git argv as `['remote', 'add', name, url]` without a `--` separator before `url`, unlike `clone()` which already guards against this pattern. `_publishRepository` in `app-store.ts` feeds `apiRepository.clone_url` (a raw string from the `createRepository` API response) directly into this sink.

### Finding Description
`git remote add` parses all arguments through `parse_options()`, which treats any token beginning with `-` as an option regardless of position, unless a `--` terminator precedes the positional arguments. `addRemote` never inserts `--`, so a `clone_url` beginning with `-` is not guaranteed to be treated as the intended positional URL argument.

### Impact Explanation
Depending on which git-remote flag is matched, this can alter remote configuration behavior unexpectedly (e.g., `-f`/`--mirror`/`-t`/`-m` accept values and change fetch/config semantics), corrupting the repository's git configuration silently — falling within the "silent corruption of what the user commits or pushes" impact category if it changes fetch refspecs or tracked branches.

### Likelihood Explanation
Requires the `createRepository` API response's `clone_url` to be attacker-influenced — realistic against a malicious/compromised GitHub Enterprise Server endpoint that the user has added as an account, since Desktop trusts the API response shape without validating `clone_url`'s scheme or leading characters. Not exploitable against github.com itself (GitHub's own API won't return such a value), so likelihood is moderate and scoped to GHE/custom-endpoint scenarios.

### Recommendation
Add a `--` separator before `url` in `addRemote` (and `setRemoteURL`), mirroring `clone.ts`'s pattern (`args.push('--', url, path)`), and/or validate that `clone_url` begins with an accepted URL scheme (`https://`, `http://`, `git://`, `ssh://`) or a valid `scp`-like host pattern before use.

### Proof of Concept
```ts
// Mock createRepository to return a hostile clone_url
const apiRepository = { clone_url: '--upload-pack=touch /tmp/pwned;true', ... }
// _publishRepository → addRemote(repository, 'origin', apiRepository.clone_url)
// results in: git(['remote', 'add', 'origin', '--upload-pack=touch /tmp/pwned;true'], ...)
// no `--` terminator present, so git's parse_options() may treat the value as an option token
```

### Citations

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/test/helpers/repository-scaffolding.ts (L34-43)
```typescript
export async function cloneRepository(
  t: TestContext,
  repository: Repository
): Promise<Repository> {
  const newDirectory = await createTempDirectory(t)

  await exec(['clone', repository.path, '--', newDirectory], __dirname)

  return new Repository(newDirectory, -2, null, false)
}
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/stores/app-store.ts (L5636-5646)
```typescript
    const apiRepository = await api.createRepository(
      org,
      name,
      description,
      private_
    )

    const gitStore = this.gitStoreCache.get(repository)
    await gitStore.performFailableOperation(() =>
      addRemote(repository, 'origin', apiRepository.clone_url)
    )
```

**File:** app/src/lib/stores/api-repositories-store.ts (L182-194)
```typescript
    const missing = new Map<string, IAPIRepository>()
    const repositories = new Map<string, IAPIRepository>()

    currentState?.repositories.forEach(r => {
      missing.set(r.clone_url, r)
      repositories.set(r.clone_url, r)
    })

    const addPage = (page: ReadonlyArray<IAPIRepository>) => {
      page.forEach(r => {
        repositories.set(r.clone_url, r)
        missing.delete(r.clone_url)
      })
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
