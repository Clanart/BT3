# Q3285: NEAR foreign-to-foreign bridge forwarding fast path creates incompatible forwarded transfer

## Question
Can an unprivileged attacker exploit `public cross-chain forward path created by `fin_transfer` with non-Near recipient` so that `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain` creates a second-leg outbound transfer whose fields do not faithfully represent the fast-paid first leg, violating `foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token plus process_fin_transfer_to_other_chain`
- Entrypoint: `public cross-chain forward path created by `fin_transfer` with non-Near recipient`
- Attacker controls: origin token address, target chain, fee split, and fast-transfer status
- Exploit idea: Focus on origin-transfer-id linkage, destination nonce allocation, and relayer substitution in cross-chain fast forwarding.
- Invariant to test: foreign-to-foreign forwarding must not let mismapped assets or stale lock state create unbacked wrapped supply on a third chain
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Compare the forwarded transfer message to the fast-paid leg and assert that every economically-relevant field stays coupled.
