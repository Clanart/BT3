# Q1305: NEAR foreign-to-foreign bridge forwarding one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public cross-chain forward path created by `fin_transfer` with non-Near recipient` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` violate `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain` in the `one inbound event spawns multiple outbound obligations` attack class because maps a foreign asset into its target-chain representation and either stores or fast-resolves a new pending transfer without ever landing value on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
