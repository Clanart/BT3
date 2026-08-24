This confirms the full path: a malicious tag ref such as `(tag: --upload-pack=/tmp/evil)` embedded in a fetched repository's commit graph is parsed by `getCommits` in `app/src/lib/git/log.ts` (splitting `%D` on `", "` and stripping the `tag: ` prefix into `commit.tags`), surfaced in the UI's context menu (`getDeleteTagsMenuItem` in `app/src/ui/history/commit-list.tsx`), and passed verbatim as `tagName` through `dispatcher.deleteTag` into `deleteTag` in `app/src/lib/git/tag.ts`, which builds `const args = ['tag', '-d', name]` with no `--` separator before `name`. The same missing-separator pattern exists in `createTag`'s `['tag', '-a', '-m', '', name, targetCommitSha]`.

### Title
Missing `--` end-of-options separator allows tag-name argv/flag injection into `git tag` - (File: app/src/lib/git/tag.ts)

### Summary
`deleteTag` and `createTag` construct `git tag` argv with the attacker-influenced tag name as a bare, unguarded token instead of an operand disambiguated by `--`. Other git wrapper functions in this codebase (e.g. `checkoutBranch`/`checkoutPaths`/`checkoutConflictedFile` in `app/src/lib/git/checkout.ts`) consistently append a `--` separator before ref/path operands to prevent option injection, but `tag.ts` does not follow this pattern. [1](#0-0) [2](#0-1) 

### Finding Description
`getCommits` parses the `%D` ref-decoration field of `git log` and extracts tag names by splitting on `", "` and stripping the `tag: ` prefix, with no validation that the resulting string doesn't begin with `-`: [3](#0-2) 

These names become `Commit.tags`, which flow to `app/src/ui/history/commit-list.tsx`'s `getDeleteTagsMenuItem`, and ultimately to `deleteTag`/`createTag` in `app/src/lib/git/tag.ts` as the bare `name` argv element with no preceding `--`: [1](#0-0) 

Because there is no `--` end-of-options marker, if `name` begins with `-`, `git tag` will attempt to parse it as an option rather than a tag-name operand, corrupting the intended option/operand argv boundary.

### Impact Explanation
The concrete PoC value `--upload-pack=/tmp/evil` is not itself a recognized flag of `git tag` (that flag belongs to `clone`/`fetch`), so this specific payload would most likely just produce a "git-tag: unknown option" error rather than executing an attacker command. I could not confirm within this investigation that any *actual* `git tag` flag (e.g. one accepting a path/command value) allows escalation to arbitrary file write or code execution — that would require enumerating `git-tag`'s real flag surface (e.g., `--cleanup=<mode>`, `--sort=<key>` affect only tag semantics, not execution). Absent a concrete `git tag` flag with dangerous side effects, the demonstrated issue is a genuine missing-`--`-separator defect (an architectural inconsistency vs. `checkout.ts`), but the "arbitrary flag injection into git tag" impact is currently unproven to reach code execution, file write/read outside the repo, or credential exfiltration as required by the bounty's Valid Impact criteria.

### Likelihood Explanation
Reachability is real and low-effort for an attacker: any repository the victim clones/fetches can embed a tag pointing at a reachable commit with a `-`-prefixed name, and the UI will display and offer to delete such tags without normalizing the name; the bounty's own note is correct that this list of tags comes from parsing untrusted `%D` output with no leading-dash guard.

### Recommendation
Add a `--` separator before the `name` operand in both `deleteTag` (`['tag', '-d', '--', name]`) and `createTag` (`['tag', '-a', '-m', '', '--', name, targetCommitSha]`), matching the pattern already used in `checkout.ts`, `apply.ts`, `reset.ts`, etc. Additionally consider rejecting/flagging tag names beginning with `-` earlier in `getCommits`/`getAllTags` for defense in depth.

### Proof of Concept
1. In a scratch repo, create a commit and a tag literally named `--upload-pack=/tmp/evil` (git's ref-name rules do not forbid a leading `-` in `check-ref-format`).
2. Fetch/clone this repository into GitHub Desktop; `getCommits` will parse this into `commit.tags` via the `%D`-splitting logic at `app/src/lib/git/log.ts:177-185`.
3. In the commit list context menu, select "Delete tag…" for this tag; this invokes `onDeleteTag(tagName)` → `dispatcher.deleteTag` → `deleteTag(repository, name)` in `app/src/lib/git/tag.ts:29-36`.
4. Instrument/log the `args` array built at line 33 (`['tag', '-d', name]`) and confirm `name` occupies the position immediately after `-d` with no preceding `--`, i.e., `git tag -d --upload-pack=/tmp/evil` is what's actually executed — demonstrating the missing operand/option boundary. Note: to escalate this PoC into a genuine security bounty finding, the reporter still needs to identify a real `git-tag` option whose value produces file write, code execution, or credential exfiltration; the given `--upload-pack` example does not do so for the `tag` subcommand.

### Citations

**File:** app/src/lib/git/tag.ts (L13-36)
```typescript
export async function createTag(
  repository: Repository,
  name: string,
  targetCommitSha: string
): Promise<void> {
  const args = ['tag', '-a', '-m', '', name, targetCommitSha]

  await git(args, repository.path, 'createTag')
}

/**
 * Delete a tag.
 *
 * @param repository        - The repository in which to create the new tag.
 * @param name              - The name of the tag to delete.
 */
export async function deleteTag(
  repository: Repository,
  name: string
): Promise<void> {
  const args = ['tag', '-d', name]

  await git(args, repository.path, 'deleteTag')
}
```

**File:** app/src/lib/git/checkout.ts (L24-36)
```typescript
function getCheckoutArgs(progressCallback?: ProgressCallback) {
  return ['checkout', ...(progressCallback ? ['--progress'] : [])]
}

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

**File:** app/src/lib/git/log.ts (L177-185)
```typescript
  return parsed.map(commit => {
    // Ref is of the format: (HEAD -> master, tag: some-tag-name, tag: some-other-tag,with-a-comma, origin/master, origin/HEAD)
    // Refs are comma separated, but some like tags can also contain commas in the name, so we split on the pattern ", " and then
    // check each ref for the tag prefix. We used to use the regex /tag: ([^\s,]+)/g)`, but will clip a tag with a comma short.
    const tags = commit.refs
      .toString()
      .split(', ')
      .flatMap(ref => (ref.startsWith('tag: ') ? ref.substring(5) : []))

```
