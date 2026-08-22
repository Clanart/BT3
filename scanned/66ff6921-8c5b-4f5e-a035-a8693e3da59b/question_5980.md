# Q5980: escapeGitSpecialCharacters: path/symlink containment escape

## Question
Does `escapeGitSpecialCharacters` in [app/src/lib/git/gitignore.ts] join or normalize an attacker-controlled repository path without confirming it stays under the repo root, allowing a checkout/apply/stash flow to place executable content in an autostart or config location?

## Target
- File/function: [app/src/lib/git/gitignore.ts] — `escapeGitSpecialCharacters`
- Entrypoint: Cloning, fetching, checking out, or opening an attacker-authored repository
- Attacker controls: repository tree contents, file paths, symlinks, `.gitattributes`/`.gitmodules`, submodule/LFS metadata
- Exploit idea: Does `escapeGitSpecialCharacters` in [app/src/lib/git/gitignore.ts] join or normalize an attacker-controlled repository path without confirming it stays under the repo root, allowing a checkout/apply/stash flow to place executable content in an autostart or config location?
- Invariant to test: every path Desktop writes, reads, or executes stays inside the selected repository working tree
- Expected Immunefi impact: Critical - arbitrary file write/replace or code execution outside the working tree on the user's machine (target scope: "Critical. Content of a repository the user merely clones, fetches, checks out, or opens - paths, symlinks, `.gitattribut...")
- Fast validation: Craft a repo with the hostile path/symlink, run the flow through this function in a unit test, assert the resolved target escapes the repo root
