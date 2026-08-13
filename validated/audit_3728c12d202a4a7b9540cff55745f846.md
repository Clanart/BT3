### Title
Unbounded transcript read in `_extract_field` can trigger `MemoryError`, which is fail-open caught by `stop.py`, bypassing Stop-hook block rules - ([File: plugins/hookify/core/rule_engine.py])

### Finding Description
`RuleEngine._extract_field` handles the `transcript` field by opening `transcript_path` and calling `f.read()` to load the *entire* transcript file into memory in one shot, with no size cap or streaming/chunked read: [1](#0-0) 

The exception handling around this read only covers `FileNotFoundError`, `PermissionError`, `(IOError, OSError)`, and `UnicodeDecodeError` — it does not catch `MemoryError`. If the transcript file is extremely large (e.g. because the agent's conversation transcript embeds huge attacker-influenced content, such as the full output of `cat`-ing a large repo file or a tool response), `f.read()` can raise `MemoryError`, which propagates unhandled out of `_extract_field` → `_check_condition` → `_rule_matches` → `evaluate_rules`.

`stop.py`'s `main()` wraps the entire rule evaluation in a broad `except Exception as e` that treats *any* exception as fail-open, printing a generic `systemMessage` and always exiting `0` without emitting a `"decision": "block"`: [2](#0-1) 

Since `MemoryError` is a subclass of `Exception` in Python, it is caught by this handler, so any Stop rule that depends on inspecting the `transcript` field (e.g. "don't stop until tests pass" style rules) silently fails open instead of enforcing its block decision.

### Impact Explanation
This is a resource-exhaustion-induced control-flow bypass: an attacker who can cause sufficiently large content to be embedded into the conversation transcript can force a `MemoryError` during transcript inspection, causing the Stop hook's fail-open path to run instead of a legitimate block decision. Scoped impact is a DoS-induced approval/block bypass on `transcript`-based Stop rules, not direct code execution or secret exfiltration.

### Likelihood Explanation
Exploitability requires the attacker to get very large content embedded into the transcript (multiple GB, depending on host memory) before the Stop hook fires — a real but comparatively high-effort precondition compared to typical injection-style bugs. It's also worth noting that `stop.py`'s fail-open behavior on *all* exceptions is an explicit, intentional design choice (comment: "On any error, allow the operation"), and the unbounded read is the more specific/fixable defect here.

### Recommendation
Cap the transcript read size (e.g., read only the last N KB/MB relevant to rule matching, or stream/chunk the read with a max-size guard) in `_extract_field`, and add an explicit `except MemoryError` (or broader resource-error) branch that fails closed (returns a value that still allows block rules to fire, or causes `stop.py` to block rather than allow) for the `transcript` field specifically.

### Proof of Concept
Integration test: mock `open()`/`os.path.getsize()` for `transcript_path` to simulate a file whose read raises `MemoryError` (or use a huge generated fixture file if resources allow), invoke `stop.py`'s `main()` with a Stop rule containing a `transcript`-field block condition, and assert the hook output does NOT fall back to the generic fail-open `systemMessage` unblocked response — i.e., assert some bounded/streamed read path still allows the block condition to be evaluated instead of raising `MemoryError` up to `stop.py`'s catch-all.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L207-225)
```python
            elif field == 'transcript':
                # Read transcript file if path provided
                transcript_path = input_data.get('transcript_path')
                if transcript_path:
                    try:
                        with open(transcript_path, 'r') as f:
                            return f.read()
                    except FileNotFoundError:
                        print(f"Warning: Transcript file not found: {transcript_path}", file=sys.stderr)
                        return ''
                    except PermissionError:
                        print(f"Warning: Permission denied reading transcript: {transcript_path}", file=sys.stderr)
                        return ''
                    except (IOError, OSError) as e:
                        print(f"Warning: Error reading transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
                    except UnicodeDecodeError as e:
                        print(f"Warning: Encoding error in transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
```

**File:** plugins/hookify/hooks/stop.py (L32-55)
```python
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Load stop rules
        rules = load_rules(event='stop')

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        # On any error, allow the operation
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)
```
