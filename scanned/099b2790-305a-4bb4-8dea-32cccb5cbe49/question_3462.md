# Q3462: output_message_to_l1_hashes output-mode split in os_utils__virtual.cairo (mode/version split)

## Question
Can a malicious L1-to-L2 message sender controlling the message payload and timing use message payloads, message ordering, message-triggered calldata, declared class contents, entry-point tables, compiled class facts to make `output_message_to_l1_hashes` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo` make full_output/compressed/KZG mode flags describe one published data shape while the emitted data follows another around L1/L2 message uniqueness and accounting, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo:15 :: output_message_to_l1_hashes
- Entrypoint: malicious L1-to-L2 message sender controlling the message payload and timing
- Attacker controls: message payloads, message ordering, message-triggered calldata, declared class contents, entry-point tables, compiled class facts
- Exploit idea: desynchronize mode flags from the data-availability or message serialization that downstream consumers actually parse while this function is handling L1/L2 message uniqueness and accounting. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: all honest consumers must derive the same published state diff and message set from a given OS output header and mode flags All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: cross-check full-output, compressed, encrypted, and KZG-enabled outputs for the same state diff and assert mode flags are sufficient to reconstruct one canonical public output Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
