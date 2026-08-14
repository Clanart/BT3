### Title
Leading-zero octal misinterpretation in `MAX_ITERATIONS`/`ITERATION` arithmetic bypasses regex-validated numeric bound, causing Ralph loop to never terminate - ([File: plugins/ralph-wiggum/hooks/stop-hook.sh])

### Summary
The stop hook validates `ITERATION` and `MAX_ITERATIONS` with the regex `^[0-9]+$` before using them in bash arithmetic comparisons (`[[ $MAX_ITERATIONS -gt 0 ]]`, `[[ $ITERATION -ge $MAX_ITERATIONS ]]`, `$((ITERATION + 1))`). Bash's arithmetic evaluator treats any decimal string with a leading `0` as octal, so a value like `018` or `09` passes the digit-only regex but is not valid in base 8, causing a runtime arithmetic error at comparison time. Because that comparison is used directly as an `if` condition, the error is swallowed as a failed (`false`) test rather than aborting the script under `set -e`, so the max-iteration guard silently never fires.

### Finding Description
The hook extracts `MAX_ITERATIONS` and `ITERATION` straight from an attacker-controlled `.claude/ralph-loop.local.md` frontmatter via `grep`/`sed` [1](#0-0) , then validates each with the ASCII-digit-only regex `^[0-9]+$` [2](#0-1) , and finally uses the raw string values directly in a bash arithmetic test to decide loop termination:

```
if [[ $MAX_ITERATIONS -gt 0 ]] && [[ $ITERATION -ge $MAX_ITERATIONS ]]; then
``` [3](#0-2) 

The regex only guarantees the string is composed of ASCII digits — it does not exclude a leading `0`. Bash's `[[ ... -gt/-ge ... ]]` numeric comparisons perform C-style arithmetic evaluation on their operands, where a leading `0` signals octal interpretation. A value such as `max_iterations: 018` (or `09`, `08`, etc.) satisfies `^[0-9]+$` but contains a digit (`8`/`9`) invalid in base 8. Evaluating `[[ 018 -gt 0 ]]` produces a bash runtime error ("value too great for base") and the test evaluates to non-zero/false rather than raising a script-fatal error, because the arithmetic expression sits inside the condition of an `if`, which is one of the constructs exempted from `set -e` termination. The `&&`-chained condition at line 51 therefore evaluates as false on every single iteration, so the branch that removes the state file and stops the loop (lines 52–54) never executes — regardless of how large `ITERATION` grows. The digit-only regex check gives a false sense of safety: it validates "looks numeric" but not "is a value bash's arithmetic evaluator will interpret as the same number", which is exactly the class of bug the question describes.

### Impact Explanation
This breaks the deny-means-deny / stop-control invariant of the Ralph Wiggum loop: a `ralph-loop.local.md` crafted (or checked in) with a leading-zero, octal-invalid `max_iterations` value causes the hook's bound check to permanently fail, so the loop keeps blocking `Stop` and feeding the prompt back to Claude indefinitely, i.e., uncontrolled automation continues past the value the user configured and past user consent. This matches the "loop never terminates at intended bound" impact class for automation/consent-control bypasses.

### Likelihood Explanation
The precondition is exactly what the question specifies: the attacker controls the content of the checked-in `.claude/ralph-loop.local.md` file (e.g., via a malicious repo, a compromised collaborator's commit, or a `/ralph-loop` invocation that accepts an attacker-influenced parameter written into that file). No privilege escalation is required — simply committing/writing `max_iterations: 018` (or any leading-zero value containing an 8 or 9) is sufficient, and the behavior is 100% reproducible on any standard bash (the octal-parsing behavior of `[[ ]]` arithmetic is deterministic, not version- or locale-dependent).

### Recommendation
Do not rely on raw string interpolation into bash arithmetic contexts. Either:
- Strip leading zeros before comparison (e.g. `MAX_ITERATIONS=$((10#$MAX_ITERATIONS))` using explicit base-10 forcing, which also causes a hard error — but at a point where the failure can be caught/handled explicitly rather than silently short-circuiting the `if`), or
- Tighten the validation regex to reject leading zeros for multi-digit values, e.g. `^(0|[1-9][0-9]*)$`, and explicitly force base-10 (`10#…`) everywhere the values are used in arithmetic (lines 51, 131, and the `-ge` comparison), so any residual parse failure surfaces as a clear corrupted-state error instead of a false "condition not met" result.

### Proof of Concept
Bash unit/integration test plan:
1. Create `.claude/ralph-loop.local.md` with frontmatter `iteration: 5` and `max_iterations: 018`, plus a valid prompt body and a transcript file with an assistant message that does not satisfy any completion promise.
2. Run `stop-hook.sh` repeatedly (simulating iterations), incrementing `iteration` each time via the hook's own update logic.
3. Reference oracle: parse `max_iterations` as a strict base-10 integer (`018` → `18`) and assert the hook should emit "Max iterations reached" and remove the state file once `iteration >= 18`.
4. Actual observed behavior: run `bash -c '[[ 018 -gt 0 ]]; echo $?'` and confirm it prints a "value too great for base" error to stderr and exits `1` (false), then assert that the loop keeps returning `{"decision":"block", ...}` (never stops) even after `iteration` exceeds 18, diverging from the oracle — demonstrating the max-iterations bound is never enforced.

### Citations

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L22-23)
```shellscript
ITERATION=$(echo "$FRONTMATTER" | grep '^iteration:' | sed 's/iteration: *//')
MAX_ITERATIONS=$(echo "$FRONTMATTER" | grep '^max_iterations:' | sed 's/max_iterations: *//')
```

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L28-48)
```shellscript
if [[ ! "$ITERATION" =~ ^[0-9]+$ ]]; then
  echo "⚠️  Ralph loop: State file corrupted" >&2
  echo "   File: $RALPH_STATE_FILE" >&2
  echo "   Problem: 'iteration' field is not a valid number (got: '$ITERATION')" >&2
  echo "" >&2
  echo "   This usually means the state file was manually edited or corrupted." >&2
  echo "   Ralph loop is stopping. Run /ralph-loop again to start fresh." >&2
  rm "$RALPH_STATE_FILE"
  exit 0
fi

if [[ ! "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "⚠️  Ralph loop: State file corrupted" >&2
  echo "   File: $RALPH_STATE_FILE" >&2
  echo "   Problem: 'max_iterations' field is not a valid number (got: '$MAX_ITERATIONS')" >&2
  echo "" >&2
  echo "   This usually means the state file was manually edited or corrupted." >&2
  echo "   Ralph loop is stopping. Run /ralph-loop again to start fresh." >&2
  rm "$RALPH_STATE_FILE"
  exit 0
fi
```

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L51-55)
```shellscript
if [[ $MAX_ITERATIONS -gt 0 ]] && [[ $ITERATION -ge $MAX_ITERATIONS ]]; then
  echo "🛑 Ralph loop: Max iterations ($MAX_ITERATIONS) reached."
  rm "$RALPH_STATE_FILE"
  exit 0
fi
```
