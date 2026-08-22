# Q3519: parseSingleUnfoldedTrailer: argv/flag or shell injection into git or a spawned process

## Question
Does `parseSingleUnfoldedTrailer` in [app/src/lib/git/interpret-trailers.ts] build a git or child-process argument list from attacker-controlled repository text without an `--` end-of-options guard, letting a crafted ref/path inject an extra flag?

## Target
- File/function: [app/src/lib/git/interpret-trailers.ts] — `parseSingleUnfoldedTrailer`
- Entrypoint: A ref, branch, tag, remote URL, repository/worktree path, or editor/shell argument the attacker controls
- Attacker controls: branch/tag/ref names, remote URLs, repository and worktree paths, custom-integration/editor/shell arguments
- Exploit idea: Does `parseSingleUnfoldedTrailer` in [app/src/lib/git/interpret-trailers.ts] build a git or child-process argument list from attacker-controlled repository text without an `--` end-of-options guard, letting a crafted ref/path inject an extra flag?
- Invariant to test: attacker-controlled text reaches a child process only as an inert operand, never as an option/flag or shell token
- Expected Immunefi impact: Critical - git or a spawned program executes attacker-chosen options or commands (target scope: "Critical. Attacker-controlled text that reaches a git or child-process invocation (ref, branch, tag, remote URL, reposit...")
- Fast validation: Feed a value beginning with `-`/`--` or containing shell metacharacters into this function in a test and assert it is passed after `--` or rejected, not interpreted as a flag
