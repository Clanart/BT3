# Q1822: NEAR sign_transfer recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public `sign_transfer` on a pending transfer id` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::sign_transfer` violate `every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent` in the `recipient or message ambiguity` attack class because loads a stored transfer, normalizes the amount using destination token decimals, parses destination message, and asks MPC to sign a `TransferMessagePayload` for the destination chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer`
- Entrypoint: `public `sign_transfer` on a pending transfer id`
- Attacker controls: transfer id, optional fee recipient, optional fee override, attached deposit, and any stored transfer fields reachable through the chosen id
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: every stored transfer must hash to one unambiguous outbound payload with one fee model, one token mapping, and one recipient intent
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
