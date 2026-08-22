# Q0550: updateSubmodulesAfterOperation: path/symlink containment escape

## Question
Can `updateSubmodulesAfterOperation` in [app/src/lib/git/submodule.ts] be made to follow an attacker-planted symlink or `..`-laden path from cloned repository content, breaking working-tree containment and letting the attacker overwrite a file the user never intended to touch?

## Target
- File/function: [app/src/lib/git/submodule.ts] — `updateSubmodulesAfterOperation`
- Entrypoint: Cloning, fetching, checking out, or opening an attacker-authored repository
- Attacker controls: repository tree contents, file paths, symlinks, `.gitattributes`/`.gitmodules`, submodule/LFS metadata
- Exploit idea: Can `updateSubmodulesAfterOperation` in [app/src/lib/git/submodule.ts] be made to follow an attacker-planted symlink or `..`-laden path from cloned repository content, breaking working-tree containment and letting the attacker overwrite a file the user never intended to touch?
- Invariant to test: every path Desktop writes, reads, or executes stays inside the selected repository working tree
- Expected Immunefi impact: Critical - arbitrary file write/replace or code execution outside the working tree on the user's machine (target scope: "Critical. Content of a repository the user merely clones, fetches, checks out, or opens - paths, symlinks, `.gitattribut...")
- Fast validation: Craft a repo with the hostile path/symlink, run the flow through this function in a unit test, assert the resolved target escapes the repo root
