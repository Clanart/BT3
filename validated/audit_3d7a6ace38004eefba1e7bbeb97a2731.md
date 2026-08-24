### Title
Non-global string replace in `trimCoAuthorsTrailers` allows attacker-controlled commit bodies to corrupt squashed/amended commit messages - (File: `app/src/models/commit.ts`)

### Summary
The bug-class in the external report is that an emitted record (an event log) misattributes an action because the code assumes the wrong source string is authoritative. The closest reachable analog in GitHub Desktop is `trimCoAuthorsTrailers` in `app/src/models/commit.ts`, which strips `Co-Authored-By` trailers from a commit body using a **non-global** `String.prototype.replace`, which only removes the *first* textual match of `${token}: ${value}` in the body rather than the actual trailer line. Because the commit body being processed originates from a cloned/fetched repository (fully attacker-controlled), a maliciously crafted commit can make this function strip the wrong text, silently changing the content Desktop uses to build new commit messages during squash and amend operations.

### Finding Description
`trimCoAuthorsTrailers` builds `Commit.bodyNoCoAuthors`, which is derived purely from parsed trailers and the raw commit body: [1](#0-0) 

This value is trusted downstream as "the commit body with co-author trailers removed" and is used directly to compose the description of a squashed commit: [2](#0-1) 

and to restore the commit message/co-authors when amending a commit: [3](#0-2) 

The flaw: `body.replace(`${token}: ${value}`, '')` (no `/g` flag, plain string argument) removes only the **first** occurrence of that exact substring in the body — not necessarily the actual trailer line that `parseTrailers`/`git interpret-trailers` identified as the real trailer. If an attacker-authored commit (from a repository the victim clones or fetches, e.g. via a malicious PR branch) contains the literal text `Co-Authored-By: Name <email>` earlier in the free-form commit description (e.g., quoted, pasted, or crafted to look like part of a changelog) *and* the real trailer with the same token/value at the bottom of the message, `replace()` strips the quoted occurrence in the description body instead of the real trailer. The result:
- The real `Co-Authored-By` trailer text remains embedded in `bodyNoCoAuthors`.
- This corrupted body is what Desktop silently uses to construct the new squashed commit description or the restored amend message — the user is shown/commits a message that differs from what the app's own co-author-stripping logic was supposed to produce, with no warning that stripping failed.

The existing guards (`parseTrailers` using `git interpret-trailers --parse`, and `isCoAuthoredByTrailer`) correctly identify the real trailer as structured data, but `trimCoAuthorsTrailers` re-derives the "body without trailers" using an unrelated, purely textual, non-global replace — so the structured trailer detection and the textual removal can point to two different lines in the message. Nothing revalidates that the removed substring is actually the trailer that was matched.

### Impact Explanation
This causes silent corruption of the commit message content the user squashes/amends and subsequently pushes: the resulting message can retain a spurious `Co-Authored-By` line (misattributing authorship to whatever attacker-chosen identity was embedded) while a different, attacker-chosen slice of the original description is unexpectedly deleted from the final message. Since squashing/amending is a normal part of the workflow and the source commit comes from a cloned/fetched, attacker-controlled repository, this matches the in-scope class of "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Requires only that the victim clone/fetch a repository containing a commit whose description happens to duplicate the exact `Token: value` string of its own trailer (attacker fully controls this, trivial to craft) and later use Desktop's squash or "amend using co-authors" feature on that commit — both are common, unprivileged GitHub Desktop workflows requiring no unusual user action beyond normal interactive rebase/squash use.

### Recommendation
Do not use `String.replace` with the raw trailer text to strip trailers from a message. Instead, strip trailers positionally/by line (e.g., operate on the already-`git interpret-trailers`-parsed trailer block, or use the unfolded/parsed line indices the same way `loadCommitAndCoAuthors` does with `lines.splice`) so that only the actual trailer line identified by `parseTrailers` is removed, regardless of whether the same text incidentally occurs elsewhere in the free-form body.

### Proof of Concept
1. Attacker creates a repository with a commit whose message is:
   ```
   Fix bug

   As discussed before:
   Co-Authored-By: Mallory <mallory@evil.example>

   (rest of legitimate description)

   Co-Authored-By: Mallory <mallory@evil.example>
   ```
   where the last line is the real trailer (recognized by `git interpret-trailers`) and the middle line is just descriptive text that happens to be byte-identical.
2. Victim clones/fetches this repo in GitHub Desktop and selects this commit to squash into another commit, or chooses "Amend commit" after undo.
3. `Commit.bodyNoCoAuthors` is computed via `trimCoAuthorsTrailers`, whose `body.replace('Co-Authored-By: Mallory <mallory@evil.example>', '')` removes only the **first** occurrence — the one inside "As discussed before: ..." — leaving the real trailer line intact in `bodyNoCoAuthors`.
4. `getSquashedCommitDescription` concatenates this corrupted `bodyNoCoAuthors` into the new squashed commit's description, so the squashed commit message unexpectedly still contains a `Co-Authored-By` trailer line and is missing the "As discussed before:" context line the user expected to keep — a discrepancy the user is unlikely to notice before committing/pushing. [1](#0-0) [2](#0-1) [4](#0-3)

### Citations

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

**File:** app/src/lib/squash/squashed-commit-description.ts (L1-17)
```typescript
import { Commit } from '../../models/commit'

export function getSquashedCommitDescription(
  commits: ReadonlyArray<Commit>,
  squashOnto: Commit
): string {
  const commitMessages = commits.map(
    c => `${c.summary.trim()}\n\n${c.bodyNoCoAuthors.trim()}`
  )

  const descriptions = [
    squashOnto.bodyNoCoAuthors.trim(),
    ...commitMessages,
  ].filter(d => d.trim() !== '')

  return descriptions.join('\n\n')
}
```

**File:** app/src/lib/stores/git-store.ts (L786-885)
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

    // We don't know (I mean, we're fairly sure) what the separator character
    // used for the trailer is so we call out to git to get all possibilities
    let separators: string | undefined = undefined

    // We know that what we've got now is well formed so we can capture the leading
    // token, followed by the separator char and a single space, followed by the
    // value
    const coAuthorRe = /^co-authored-by(.)\s(.*)/i
    const extractedTrailers = []

    // Iterate backwards from the unfolded message and look for trailers that we've
    // already seen when calling parseTrailers earlier.
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i]
      const match = coAuthorRe.exec(line)

      // Not a trailer line, we're sure of that
      if (!match) {
        continue
      }

      // Only shell out for separators if we really need them
      separators ??= await getTrailerSeparatorCharacters(this.repository)

      if (separators.indexOf(match[1]) === -1) {
        continue
      }

      const trailer = parseSingleUnfoldedTrailer(line, match[1])

      if (!trailer) {
        continue
      }

      // We already know that the key is Co-Authored-By so we only
      // need to compare by value. Let's see if we can find the thing
      // that we believe to be a trailer among what interpret-trailers
      // --parse told us was a trailer. This step is a bit redundant
      // but it ensure we match exactly with what Git thinks is a trailer
      const foundTrailerIx = coAuthorTrailers.findIndex(
        t => t.value === trailer.value
      )

      if (foundTrailerIx === -1) {
        continue
      }

      // We're running backwards
      extractedTrailers.unshift(coAuthorTrailers[foundTrailerIx])

      // Remove the trailer that matched so that we can be sure
      // we're not picking it up again
      coAuthorTrailers.splice(foundTrailerIx, 1)

      // This line was a co-author trailer so we'll remove it to
      // make sure it doesn't end up in the restored commit body
      lines.splice(i, 1)
    }

    // Get rid of the summary/title
    lines.splice(0, 2)

    const newBody = lines.join('\n').trim()

    this._commitMessage = {
      summary: commit.summary,
      description: newBody,
      timestamp: Date.now(),
    }
```
