# Q3723: os_carried_outputs_new output-mode split in output.cairo (nested-call revert edge)

## Question
Can a malicious user transaction set shaping storage writes, messages, class declarations, and block-level state diffs use message payloads, message ordering, message-triggered calldata, the shape of the resulting state diff through crafted valid transactions to make `os_carried_outputs_new` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo` make full_output/compressed/KZG mode flags describe one published data shape while the emitted data follows another around L1/L2 message uniqueness and accounting, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can the discrepancy be triggered only after a nested call, constructor call, or inner revert changes ownership of side effects?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo:147 :: os_carried_outputs_new
- Entrypoint: malicious user transaction set shaping storage writes, messages, class declarations, and block-level state diffs
- Attacker controls: message payloads, message ordering, message-triggered calldata, the shape of the resulting state diff through crafted valid transactions
- Exploit idea: desynchronize mode flags from the data-availability or message serialization that downstream consumers actually parse while this function is handling L1/L2 message uniqueness and accounting. Drive the bug only after nested execution crosses a caller/callee or revert boundary.
- Invariant to test: all honest consumers must derive the same published state diff and message set from a given OS output header and mode flags Nested execution and rollback must preserve the same ownership and rollback invariant as the flat path.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: cross-check full-output, compressed, encrypted, and KZG-enabled outputs for the same state diff and assert mode flags are sufficient to reconstruct one canonical public output Construct a nested-call test with mixed success/failure outcomes and assert state, messages, and class changes stay attributable and revertable.
