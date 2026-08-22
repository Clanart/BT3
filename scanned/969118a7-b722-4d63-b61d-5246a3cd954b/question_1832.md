# Q1832: access: path/symlink containment escape

## Question
Can an unprivileged attacker who authors a repository the victim clones drive `access` in [app/src/lib/path-exists.ts] with a path or symlink that resolves outside the repository working tree, so Desktop writes or replaces a file on the host outside the intended directory?

## Target
- File/function: [app/src/lib/path-exists.ts] — `access`
- Entrypoint: Cloning, fetching, checking out, or opening an attacker-authored repository
- Attacker controls: repository tree contents, file paths, symlinks, `.gitattributes`/`.gitmodules`, submodule/LFS metadata
- Exploit idea: Can an unprivileged attacker who authors a repository the victim clones drive `access` in [app/src/lib/path-exists.ts] with a path or symlink that resolves outside the repository working tree, so Desktop writes or replaces a file on the host outside the intended directory?
- Invariant to test: every path Desktop writes, reads, or executes stays inside the selected repository working tree
- Expected Immunefi impact: Critical - arbitrary file write/replace or code execution outside the working tree on the user's machine (target scope: "Critical. Content of a repository the user merely clones, fetches, checks out, or opens - paths, symlinks, `.gitattribut...")
- Fast validation: Craft a repo with the hostile path/symlink, run the flow through this function in a unit test, assert the resolved target escapes the repo root
