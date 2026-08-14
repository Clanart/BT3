### Title
Fragile ordering-dependent routing in compound Bash commit/push security review may silently skip commit review - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Summary
The `security-guidance` plugin's `hooks.json` registers two independent `PostToolUse` matchers on `Bash` — one keyed on `if: "Bash(git commit:*)"` and one on `if: "Bash(git push:*)"` — both invoking the same script, `security_reminder_hook.py`. For a compound command like `git commit -m x && git push`, Claude Code evaluates each `if` independently and spawns the hook script once per match, so the script is invoked twice for the same `tool_use_id` with no way to tell which `if` condition triggered it. The routing logic in `main()` therefore relies entirely on an implicit, undocumented-to-the-runtime ordering assumption ("check commit first") to guarantee commit review always executes on compound commit+push commands, deduping the second invocation via a claim/sentinel race.

### Finding Description
In `main()`, the routing block explicitly documents this implicit constraint: [1](#0-0) 

The comment states plainly: "Routing therefore MUST check commit FIRST so that compound commit+push commands continue to hit commit-review... The alternative — checking push first — would silently DROP commit-review on `git commit && git push`, which is a regression."

This is structurally identical to the Ladle batching bug class: a critical safety invariant (here, "commit review must always run before/instead-of push-sweep on a compound command, and must run exactly once") is enforced only by the order of `if`/`elif` branches in one function, not by any structural guarantee tying it to the actual `hooks.json` `if:`-condition dispatch order. Nothing in the code cross-checks that hooks.json's registration order matches this hard-coded assumption, and the correctness depends on:
1. The regex ordering (`_GIT_COMMIT_RE.search(cmd) or _GIT_PUSH_RE.search(cmd)` then `if _GIT_COMMIT_RE... elif _GIT_PUSH_RE...`) in `security_reminder_hook.py` never being reordered.
2. `_claim_bash_hook_once` correctly winning/losing the sentinel race regardless of which of the two spawned processes (commit-matcher or push-matcher invocation) reaches the claim first — the comment assumes this works "because" commit is checked first in the *surviving* process, not because the *claiming* process is deterministically the commit one.

I was not able to fully verify the implementation of `_claim_bash_hook_once` (its file-locking/race semantics) or `handle_commit_review_posttooluse`/`handle_push_sweep_posttooluse` within the available context — the grep for their definitions returned matches but the tool budget was exhausted before their bodies could be read. This limits certainty about whether the claim mechanism deterministically picks the commit-invocation as the "winner," or whether it is actually a race between two OS processes where the push-matcher spawn could occasionally claim the sentinel first and call `handle_push_sweep_posttooluse` instead of commit review, silently skipping the commit-content security scan for that commit.

### Impact Explanation
If the race is not actually deterministic (i.e., the *first process to acquire the claim* runs whichever handler its own `if _GIT_COMMIT_RE... elif` picks, but nothing forces the commit-matcher-spawned process specifically to win the race), then under load or scheduling jitter the push-matcher-invocation could win the claim and only the push-sweep path runs, meaning the just-made commit's diff is never sent through the LLM-based `handle_commit_review_posttooluse` security scan before push. Since this reviewer is documented elsewhere in the file as the mechanism that flags secrets/vulnerabilities in commits, a missed invocation is a silent security-control bypass: potentially sensitive or vulnerable code could be pushed without the intended review gate ever firing, with only a generic "push sweep" fallback (whose behavior on this specific commit is unclear from the reachable code) covering the gap.

### Likelihood Explanation
Likelihood is low-to-moderate: this requires either (a) a future refactor that reorders the `if commit / elif push` checks without noticing the documented invariant (a realistic maintenance risk explicitly flagged in the code's own comments), or (b) the claim/sentinel mechanism in `_claim_bash_hook_once` not being as deterministic as assumed for concurrent spawns triggered by the same compound Bash call. I could not confirm from the available code whether (b) is already a live race condition or a correctly-solved problem, since the function body was not retrievable in the remaining budget.

### Recommendation
- Make the correctness of "commit review always fires exactly once on compound commit+push" independent of source-line ordering: e.g., encode command-type priority as an explicit, tested constant/table (`_ROUTE_PRIORITY = ["commit", "push"]`) rather than relying on `if/elif` sequence, and add a unit test asserting that a compound `git commit && git push` invocation always results in exactly one `handle_commit_review_posttooluse` call regardless of which spawned process wins the claim race.
- Verify and, if necessary, harden `_claim_bash_hook_once` so the winner is deterministically the commit-matcher invocation when both commit and push patterns match the same command (e.g., have the push-spawned process defer/back off when it detects the command also matches the commit pattern, rather than relying on a symmetric race).
- Add a regression test that simulates two near-simultaneous invocations for the same `tool_use_id` (one from each `if` matcher) and asserts commit-review output is produced exactly once and push-sweep is skipped for that call, per the documented intended behavior.

### Proof of Concept
Not independently reproducible from the available static context — reproducing this would require exercising `_claim_bash_hook_once` under concurrent invocation to determine whether the "commit wins" assumption holds only because of process/line ordering or is actually guaranteed by a deterministic tie-break. This is flagged as a code-fragility/maintenance-risk finding backed by the code's own explicit warning comment at [2](#0-1) , not a demonstrated live exploit.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2074-2109)
```python
    # Handle PostToolUse[Bash] — commit review or push sweep (asyncRewake).
    #
    # hooks.json has two `if` configs under the Bash matcher (`git commit:*`
    # and `git push:*`). CC evaluates each `if` independently and spawns this
    # script ONCE PER MATCH — so `git commit -m x && git push` spawns python
    # twice with the same command string and the same tool_use_id. The python
    # cannot tell which `if` fired it.
    #
    # Routing therefore MUST check commit FIRST so that compound commit+push
    # commands continue to hit commit-review (the pre-existing behaviour) on
    # the commit-matcher invocation. The push-matcher invocation of the SAME
    # compound command is deduped by `_claim_bash_hook_once` below: the second
    # spawn loses the tool_use_id sentinel race and exits early with
    # `bash_hook_dedup`, so commit-review runs exactly once. The alternative —
    # checking push first — would silently DROP commit-review
    # on `git commit && git push`, which is a regression.
    #
    # The push-sweep does NOT run on the compound call. That's acceptable: the
    # just-made commit is recorded by commit-review, so the next standalone
    # push sees it as reviewed and the sweep base advances past it. Older
    # unreviewed commits in the range are caught on that next push.
    if tool_name == "Bash" and hook_event_name == "PostToolUse":
        cmd = (input_data.get("tool_input") or {}).get("command", "") or ""
        if not (_GIT_COMMIT_RE.search(cmd) or _GIT_PUSH_RE.search(cmd)):
            return
        if not _claim_bash_hook_once(input_data):
            # Another spawn for this same tool_use_id already claimed the
            # work (compound matched multiple `if` configs). Emit a single
            # metric so telemetry can count how often the de-dupe kicks in.
            print(json.dumps({"metrics": {"bash_hook_dedup": True}}), flush=True)
            sys.exit(0)
        if _GIT_COMMIT_RE.search(cmd):
            handle_commit_review_posttooluse(input_data)
        elif _GIT_PUSH_RE.search(cmd):
            handle_push_sweep_posttooluse(input_data)
        return
```
