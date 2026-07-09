# Q777: NEAR omni-types address/string utilities one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public transfer, proof, and token-mapping flows through every chain adapter` and make `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs`` both release local value and create a second valid outbound bridge obligation via converts user-controlled recipient strings and proof-derived addresses into typed `OmniAddress` values used throughout the bridge, violating `cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account`?

## Target
- File/function: `near/omni-types/src/lib.rs plus `hex_types.rs`, `sol_address.rs`, and `utils.rs``
- Entrypoint: `public transfer, proof, and token-mapping flows through every chain adapter`
- Attacker controls: address strings, hex encodings, account ids, chain-kind tags, and recipient parsing across chains
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: cross-chain address normalization must never let two textual encodings collapse onto one asset identity or one textual recipient resolve to the wrong chain/account
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
