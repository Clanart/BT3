### Title
Unverified `Co-Authored-By` trailer identity from a fetched commit is silently injected into the user's own amended commit - ([File: app/src/lib/stores/git-store.ts])

### Summary
The RaptorCast finding is about trusting an unauthenticated identity field (`validator_id`/`node_id`) embedded in a message payload instead of verifying it against the cryptographically-recovered signer. The closest analog in GitHub Desktop is `loadCommitAndCoAuthors`/`restoreCoAuthorsFromCommit` in `git-store.ts`, which extracts `Co-Authored-By: Name <email>` trailers from an arbitrary commit's message body — plain text fully controlled by whoever authored that commit — and feeds that name/email straight into the co-author list of the commit the *user* is about to create via amend, with no verification that the claimed identity has any relationship to a real account or to the commit's actual author/committer identity.

### Finding Description
When a user chooses to amend a commit, `GitStore.prepareToAmendCommit` calls `restoreCoAuthorsFromCommit`, which calls `loadCommitAndCoAuthors`: [1](#0-0) 

That function extracts `Co-Authored-By` trailers from the commit body using `parseTrailers`/`GitAuthor.parse`, which performs only a loose regex match (`/^(.*?)\s+<(.*?)>/`) with no validation of the values, and pushes the parsed name/email directly into `this._coAuthors` as a `'known'` author: [2](#0-1) [3](#0-2) 

Those `coAuthors` are subsequently rendered in the commit UI and, on confirmation, are re-serialized as `Co-Authored-By` trailers on the *new* commit the user creates: [4](#0-3) 

Crucially, there is no check that the recovered/claimed co-author name+email corresponds to any verified GitHub account, to the commit's actual author identity, or to anything cryptographically attested (e.g. GPG/SSH commit signature). Exactly like the RaptorCast bug — where `validator_id`/`node_id` fields inside a message were trusted without checking them against the signature that was actually verified — Desktop trusts an arbitrary plaintext identity claim embedded inside commit content, sourced from a possibly-attacker-controlled repository (a cloned/fetched fork, a malicious PR branch, or a tampered remote), and lets it flow forward into the user's own authored history.

### Impact Explanation
If a user fetches/checks out a branch from an untrusted remote (e.g. reviewing a fork PR) that contains a commit with a forged `Co-Authored-By: Some Trusted Maintainer <email@org>` trailer, and later amends that commit in Desktop (a very common workflow — fixing a typo, squashing, editing message), Desktop will silently re-attribute the forged co-author onto the commit the user pushes, without any warning that this "co-author" claim originated from unverified, attacker-supplied text rather than from the user's own input or a verified account lookup. This is a silent corruption of what the user commits/pushes: the resulting commit history (which may be pushed and become part of the public/shared record) contains attacker-chosen identity data that the user never typed and had no reason to inspect (trailers are hidden from the summary/description shown in the UI, only appearing as parsed "co-author" avatars). Compared to the RaptorCast DoS (medium-low impact per the original report), this is a comparable "identity field trusted without verification" class of bug, though the blast radius here is limited to co-author metadata rather than commit content/executable data.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to (1) have a `gitHubRepository` associated (trailer-restoration only runs in that case) and (2) explicitly choose to amend a commit that isn't their own (or was fetched from an untrusted source), which is a normal but not universal workflow (e.g., "fixup" of a PR branch commit before pushing, editing a cherry-picked commit's message). No special privileges are needed by the attacker beyond crafting a commit with a forged trailer in a repository/branch the victim will fetch and interact with.

### Recommendation
When restoring co-authors from an existing commit's trailers, do not treat the parsed name/email as an unconditionally "known" author. At minimum:
- Only auto-restore co-authors when the commit being amended is the user's own last commit (already partially implied by the amend UX, but not enforced against tampering of trailers by a third party in a rebase/cherry-pick scenario).
- Cross-check the extracted name/email against the repository's known collaborators/GitHub API identities before marking the author as `'known'`, falling back to `'unknown'` (which triggers the existing "unknown co-author" confirmation dialog) for unverified entries.
- Surface a warning in the commit UI when co-author trailers are inferred from external commit content rather than user input.

### Proof of Concept
1. Attacker creates a branch/PR with a commit whose message contains:
   ```
   Some commit message

   Co-Authored-By: Trusted Maintainer <trusted@bigcorp.com>
   ```
2. Victim, using GitHub Desktop, fetches/checks out this branch (e.g., to review a PR) and decides to fix the commit message via "Amend last commit."
3. `GitStore.prepareToAmendCommit` → `restoreCoAuthorsFromCommit` → `loadCommitAndCoAuthors` parses the trailer and adds `Trusted Maintainer <trusted@bigcorp.com>` as a `'known'` co-author with no verification [2](#0-1) .
4. Victim edits only the summary and commits; `getCoAuthorTrailers` re-emits the (forged, unverified) `Co-Authored-By` trailer into the new commit [4](#0-3) .
5. The victim pushes, and the commit history now falsely attributes authorship/co-authorship to `trusted@bigcorp.com`, without either party having verified that identity.

### Citations

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

**File:** app/src/lib/stores/git-store.ts (L886-919)
```typescript

    const extractedAuthors = extractedTrailers.map(t =>
      GitAuthor.parse(t.value)
    )
    const newAuthors = new Array<Author>()

    // Last step, phew! The most likely scenario where we
    // get called is when someone has just made a commit and
    // either forgot to add a co-author or forgot to remove
    // someone so chances are high that we already have a
    // co-author which includes a username. If we don't we'll
    // add it without a username which is fine as well
    for (let i = 0; i < extractedAuthors.length; i++) {
      const extractedAuthor = extractedAuthors[i]

      // If GitAuthor failed to parse
      if (extractedAuthor === null) {
        continue
      }

      const { name, email } = extractedAuthor
      const existing = this.coAuthors
        .filter(isKnownAuthor)
        .find(a => a.name === name && a.email === email && a.username !== null)
      newAuthors.push(
        existing || { kind: 'known', name, email, username: null }
      )
    }

    this._coAuthors = newAuthors

    if (this._coAuthors.length > 0 && this._showCoAuthoredBy === false) {
      this._showCoAuthoredBy = true
    }
```

**File:** app/src/models/git-author.ts (L1-15)
```typescript
export class GitAuthor {
  public static parse(nameAddr: string): GitAuthor | null {
    const m = nameAddr.match(/^(.*?)\s+<(.*?)>/)
    return m === null ? null : new GitAuthor(m[1], m[2])
  }

  public constructor(
    public readonly name: string,
    public readonly email: string
  ) {}

  public toString() {
    return `${this.name} <${this.email}>`
  }
}
```

**File:** app/src/ui/changes/commit-message.tsx (L577-585)
```typescript
  private getCoAuthorTrailers() {
    const { coAuthors } = this.props
    const token = 'Co-Authored-By'
    return this.isCoAuthorInputEnabled
      ? coAuthors
          .filter(isKnownAuthor)
          .map(a => ({ token, value: `${a.name} <${a.email}>` }))
      : []
  }
```
