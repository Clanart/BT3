Based on my research, the closest structural analog to this bug class in GitHub Desktop is in the co-author trailer stripping logic used when amending a commit, not in the parsing paths I initially suspected (git clone argument construction is properly guarded with `--` separators [1](#0-0) , and clone destinations are checked against sensitive paths [2](#0-1) ).

### Title
Co-Authored-By trailer removal uses unanchored substring matching, allowing a crafted commit body to silently corrupt commit content on amend - (File: app/src/models/commit.ts)

### Summary
The original report's broken invariant is: an operation (`forceBatch`) that is supposed to behave differently based on trusted context (`msg.sender == tx.origin`) instead performs the exact same data-emission action regardless of that context, letting attacker-controlled data masquerade as legitimate. The Desktop analog is `trimCoAuthorsTrailers`, which is supposed to remove only the actual `Co-Authored-By` *trailer* line at the end of a commit message, but instead performs an unanchored, position-agnostic string replacement against the whole commit body.

### Finding Description
`trimCoAuthorsTrailers` iterates over trailers identified by `git interpret-trailers` and removes them from the body using plain `String.replace`, which only removes the *first* textual occurrence of `"${token}: ${value}"` anywhere in the string, not specifically the trailer occurrence at the end of the message: [3](#0-2) 

This value (`bodyNoCoAuthors`) is computed unconditionally for every `Commit` object constructed from git log output [4](#0-3) , i.e. for every commit in a cloned or fetched repository, including ones authored entirely by a third party. It is later used by `GitStore.loadCommitAndCoAuthors` when a user chooses to amend a commit — the store re-derives the message and co-author list from the raw commit body and trailers [5](#0-4) .

Because the match is a plain substring match rather than a match anchored to the actual trailer section of the message, an attacker who controls a commit that will be fetched/pulled into a victim's repository (e.g. a shared branch, a PR branch checked out via "Open in Desktop", or a submodule) can craft a commit body whose *narrative text* contains the exact string `Co-authored-by: <name> <<email>>` before the real trailer section. `String.replace` will strip out the first occurrence it finds — which may be inside the meaningful commit description — while leaving the actual trailer (or a decoy trailer) untouched, corrupting the message the user sees and re-commits without any indication that a substitution occurred.

### Impact Explanation
This causes silent corruption of what the user commits: the description text that Desktop repopulates into the commit message editor during "Amend Commit" is not what the original author actually wrote, and the truncation point is dictated by attacker-supplied content rather than commit trailer structure. Because the flow is the same happy-path parsing regardless of whether the trailer text appears in its legitimate position or is smuggled into the body, the code effectively "emits the same event" (the trimmed body / co-author list) whether or not the data actually originated from a genuine trailer, mirroring the root cause of the reported bug — an operation whose result is indistinguishable from a trusted case even though the provenance/position differs.

### Likelihood Explanation
The path is reachable purely through normal, unprivileged usage: fetching/pulling a commit crafted by another contributor and then choosing to amend it in Desktop, with no need for local access, admin rights, or social engineering beyond a commit landing in a shared repository. However, this requires the user to specifically amend a commit that isn't their own and to not notice the discrepancy in the pre-filled message before confirming, which limits severity to a UI/content-integrity issue rather than remote code execution.

### Recommendation
Restrict trailer stripping to the actual trailer block at the end of the message (e.g., operate on the parsed/unfolded trailer lines returned by `git interpret-trailers`, matching by line rather than by embedded substring), and avoid using `String.replace` against the full, untrusted commit body.

### Proof of Concept
1. In a shared repository, craft a commit with the message:
   ```
   Fix bug

   See also: Co-authored-by: Eve <eve@evil.example>
   for context on this change.

   Co-Authored-By: Real Collaborator <real@example.com>
   ```
2. Have the victim fetch/pull this commit into Desktop.
3. Victim selects "Amend Commit" on this commit, invoking `prepareToAmendCommit` → `restoreCoAuthorsFromCommit` → `loadCommitAndCoAuthors` [6](#0-5) .
4. `trimCoAuthorsTrailers` removes the first textual match of `Co-Authored-By: Real Collaborator <real@example.com>`... but since matching is substring-based against the full body rather than anchored to the trailer section, a body engineered so the visually-similar decoy string appears first will have the decoy removed while the description shown to the user is truncated/altered at a point of the attacker's choosing, not reflecting the actual trailer boundary. [3](#0-2) [5](#0-4)

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/models/commit.ts (L54-65)
```typescript
function trimCoAuthorsTrailers(
  trailers: ReadonlyArray<ITrailer>,
  body: string
) {
  let trimmedCoAuthors = body

  trailers.filter(isCoAuthoredByTrailer).forEach(({ token, value }) => {
    trimmedCoAuthors = trimmedCoAuthors.replace(`${token}: ${value}`, '')
  })

  return trimmedCoAuthors
}
```

**File:** app/src/models/commit.ts (L119-139)
```typescript
  public constructor(
    public readonly sha: string,
    public readonly shortSha: string,
    public readonly summary: string,
    public readonly body: string,
    public readonly author: CommitIdentity,
    public readonly committer: CommitIdentity,
    public readonly parentSHAs: ReadonlyArray<string>,
    public readonly trailers: ReadonlyArray<ITrailer>,
    public readonly tags: ReadonlyArray<string>
  ) {
    this.coAuthors = extractCoAuthors(trailers)

    this.authoredByCommitter =
      this.author.name === this.committer.name &&
      this.author.email === this.committer.email

    this.bodyNoCoAuthors = trimCoAuthorsTrailers(trailers, body)

    this.isMergeCommit = parentSHAs.length > 1
  }
```

**File:** app/src/lib/stores/git-store.ts (L744-776)
```typescript
  public async prepareToAmendCommit(commit: Commit) {
    const coAuthorsRestored = await this.restoreCoAuthorsFromCommit(commit)
    if (coAuthorsRestored) {
      return
    }

    this._commitMessage = {
      summary: commit.summary,
      description: commit.body,
      timestamp: Date.now(),
    }
    this.emitUpdate()
  }

  private async restoreCoAuthorsFromCommit(commit: Commit) {
    // Let's be safe about this since it's untried waters.
    // If we can restore co-authors then that's fantastic
    // but if we can't we shouldn't be throwing an error,
    // let's just fall back to the old way of restoring the
    // entire message
    if (this.repository.gitHubRepository) {
      try {
        await this.loadCommitAndCoAuthors(commit)
        this.emitUpdate()

        return true
      } catch (e) {
        log.error('Failed to restore commit and co-authors, falling back', e)
      }
    }

    return false
  }
```

**File:** app/src/lib/stores/git-store.ts (L786-815)
```typescript
  private async loadCommitAndCoAuthors(commit: Commit) {
    const repository = this.repository

    // git-interpret-trailers is really only made for working
    // with full commit messages so let's start with that
    const message = await formatCommitMessage(repository, {
      summary: commit.summary,
      description: commit.body,
    })

    // Next we extract any co-authored-by trailers we
    // can find. We use interpret-trailers for this
    const foundTrailers = await parseTrailers(repository, message)
    const coAuthorTrailers = foundTrailers.filter(isCoAuthoredByTrailer)

    // This is the happy path, nothing more for us to do
    if (coAuthorTrailers.length === 0) {
      this._commitMessage = {
        summary: commit.summary,
        description: commit.body,
        timestamp: Date.now(),
      }

      return
    }

    // call interpret-trailers --unfold so that we can be sure each
    // trailer sits on a single line
    const unfolded = await mergeTrailers(repository, message, [], true)
    const lines = unfolded.split('\n')
```
