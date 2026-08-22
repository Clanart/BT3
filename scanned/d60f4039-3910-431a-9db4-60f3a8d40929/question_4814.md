# Q4814: constructor: git/API output parsing misrepresents commit, discard, or push

## Question
Does `constructor` in [app/src/lib/stores/notifications-store.ts] split or parse git output on a delimiter an attacker can embed in a path or message, letting a crafted entry desynchronize what the user believes they are committing?

## Target
- File/function: [app/src/lib/stores/notifications-store.ts] — `constructor`
- Entrypoint: Parsing of attacker-controlled git or API output (status, diff, log, refs, submodules, ANSI logs)
- Attacker controls: crafted status/diff/log/refs bytes, filenames, submodule entries, delimiters in repository output
- Exploit idea: Does `constructor` in [app/src/lib/stores/notifications-store.ts] split or parse git output on a delimiter an attacker can embed in a path or message, letting a crafted entry desynchronize what the user believes they are committing?
- Invariant to test: parsed state exactly reflects what will be committed, discarded, pushed, or checked out
- Expected Immunefi impact: High - silent loss of local work or publication of content the user did not intend to commit/push (target scope: "High. Parsing of attacker-controlled git or API output (status, diff, log, refs, submodules, trampoline commands, ANSI l...")
- Fast validation: Feed crafted git/API output to `constructor` in a test and assert the parsed file set/state matches ground truth, with no dropped or injected entry
