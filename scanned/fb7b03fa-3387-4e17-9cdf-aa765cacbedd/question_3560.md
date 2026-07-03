# Q3560: output_message_to_l1_hashes config-hash skew in os_utils__virtual.cairo (mode/version split)

## Question
Can a malicious L1-to-L2 message sender controlling the message payload and timing use message payloads, message ordering, message-triggered calldata, declared class contents, entry-point tables, compiled class facts to make `output_message_to_l1_hashes` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo` let attacker-visible output or proof state bind to one StarkNet OS config while execution or downstream verification uses another around L1/L2 message uniqueness and accounting, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo:15 :: output_message_to_l1_hashes
- Entrypoint: malicious L1-to-L2 message sender controlling the message payload and timing
- Attacker controls: message payloads, message ordering, message-triggered calldata, declared class contents, entry-point tables, compiled class facts
- Exploit idea: break the link between the config hash in global context, proof headers, fee token address, and serialized OS output while this function is handling L1/L2 message uniqueness and accounting. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: one accepted execution must have exactly one active OS configuration binding across execution, proof validation, and public output All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: vary chain_id, fee token address, public key hash, and proof headers around this function, then assert no accepted trace can mix config values across phases Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
