## Answer

The premises in the question are correct based on the code I reviewed:

- `remote.name` in `pushRepo` is always the literal string `'origin'`, hardcoded at the call site (`const remote: IRemote = { name: 'origin', url: repo.clone_url }`), so it is not attacker-controllable. [1](#0-0) 

- `args` in `pushRepo` is built as `['push', '-u', remote.name, remoteBranchName]` — there is no `--` argument-separator anywhere in this array before the positional branch name, so `git push` will parse `remoteBranchName` using normal flag-parsing rules if it starts with `-`. [2](#0-1) 

- `remoteBranchName` is passed in as `branch`, computed as `repo.default_branch ?? (await getDefaultBranch())`, where `repo` is the object returned directly from the GitHub/GHE API's repository-creation response (`createAPIRepository` → `api.createRepository(...)`). [3](#0-2) [4](#0-3) 

- Unlike other branch-name flows in the codebase, this value is never passed through `sanitizedRefName`/`testForInvalidChars` (which strip leading `-`/`+` and other invalid ref characters) before being used as a positional git argument. [5](#0-4) 

The fallback path (`getDefaultBranch()` → local git config or hardcoded `'main'`) is safe, since it doesn't originate from attacker-controlled data. [6](#0-5) 

So yes: given `remote.name` is fixed, `remoteBranchName` is the only remaining unsanitized positional argument in this specific `git push` invocation, and there is no `--` terminator protecting it.

### Title
Unsanitized `repo.default_branch` used as positional `git push` argument without `--` terminator - (File: `app/src/lib/stores/helpers/create-tutorial-repository.ts`)

### Summary
`pushRepo` builds `git push` arguments from an API-supplied `default_branch` value with no sanitization and no `--` separator, allowing a value starting with `-` to be interpreted by git as a flag (e.g. `--upload-pack=`/`--receive-pack=`) instead of a ref name.

### Finding Description
`createTutorialRepository` takes `repo.default_branch` straight from the JSON response of `api.createRepository` and passes it, unsanitized, as `remoteBranchName` into `pushRepo`, which appends it as the last positional argument of `['push', '-u', remote.name, remoteBranchName]` with no `--` terminator. [2](#0-1) 
Other flows that use branch names as git arguments in this codebase run them through `sanitizedRefName`, which strips leading `-`/`+` characters precisely to prevent this class of bug. [5](#0-4) 
That defense is absent here, and `remote.name` being hardcoded `'origin'` does close off that argument slot, leaving `remoteBranchName` as the sole unsanitized positional value.

### Impact Explanation
If `default_branch` value in the API response is attacker-influenced (e.g., a malicious/compromised GitHub Enterprise Server the user has added as an account, or a MITM on that connection) and set to something like `--upload-pack=...` or `--receive-pack=<cmd>`, the resulting `git push` invocation could have its argv parsing hijacked, potentially leading to execution of an attacker-specified program on push (a well-known "git argument injection" primitive), i.e., code execution.

### Likelihood Explanation
Exploitability depends entirely on the attacker's ability to control the `default_branch` field of the API repository-creation response, which normally comes from the trusted GitHub/GHE endpoint the user is authenticated against. This requires either a malicious/attacker-controlled GitHub Enterprise Server account or a MITM of that API call — a non-trivial precondition compared to more direct attacker-controlled-repo-content bugs. I was not able to fully verify from the index whether the API response type/parsing layer (`app/src/lib/api.ts`) imposes any additional validation on `default_branch` before it reaches this code, due to running out of search iterations; this should be double-checked in a full codebase session.

### Recommendation
Sanitize `branch` with `sanitizedRefName` (or explicitly reject values beginning with `-`) before using it as `remoteBranchName`, and add a `--` terminator in the `pushRepo` args array (`['push', '-u', remote.name, '--', remoteBranchName]`) to guarantee git treats it as a positional ref regardless of content.

### Proof of Concept
1. Have `repo.default_branch` (from the create-repository API response) be the string `--upload-pack=touch${IFS}/tmp/pwned;`.
2. `createTutorialRepository` sets `branch` to this value and calls `pushRepo(path, account, remote, branch, ...)`. [7](#0-6) 
3. `pushRepo` executes `git push -u origin --upload-pack=touch${IFS}/tmp/pwned;` with no `--` terminator, letting git interpret the crafted string as a flag rather than a branch ref. [2](#0-1)

### Citations

**File:** app/src/lib/stores/helpers/create-tutorial-repository.ts (L25-34)
```typescript
async function createAPIRepository(account: Account, name: string) {
  const api = new API(account.endpoint, account.token)

  try {
    return await api.createRepository(
      null,
      name,
      'GitHub Desktop tutorial repository',
      true
    )
```

**File:** app/src/lib/stores/helpers/create-tutorial-repository.ts (L83-84)
```typescript
  const args = ['push', '-u', remote.name, remoteBranchName]
  await git(args, path, 'tutorial:push', pushOpts)
```

**File:** app/src/lib/stores/helpers/create-tutorial-repository.ts (L114-140)
```typescript
  const repo = await createAPIRepository(account, name)
  const branch = repo.default_branch ?? (await getDefaultBranch())
  progressCb('Initializing local repository', 0.2)

  await mkdir(path, { recursive: true })

  await git(
    ['-c', `init.defaultBranch=${branch}`, 'init'],
    path,
    'tutorial:init'
  )

  await writeFile(Path.join(path, 'README.md'), InitialReadmeContents)

  await git(['add', '--', 'README.md'], path, 'tutorial:add')
  await git(['commit', '-m', 'Initial commit'], path, 'tutorial:commit')

  const remote: IRemote = { name: 'origin', url: repo.clone_url }
  await git(
    ['remote', 'add', remote.name, remote.url],
    path,
    'tutorial:add-remote'
  )

  await pushRepo(path, account, remote, branch, (title, value, description) => {
    progressCb(title, 0.3 + value * 0.6, description)
  })
```

**File:** app/src/lib/sanitize-ref-name.ts (L8-11)
```typescript
/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```

**File:** app/src/lib/helpers/default-branch.ts (L25-27)
```typescript
export async function getDefaultBranch(): Promise<string> {
  return (await getConfiguredDefaultBranch()) ?? DefaultBranchInDesktop
}
```
