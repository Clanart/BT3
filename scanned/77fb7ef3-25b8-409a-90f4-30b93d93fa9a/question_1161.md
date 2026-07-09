# Q1161: NEAR cross-chain recipient parsing one inbound event spawns multiple outbound obligations through cross-module drift

## Question
Can an unprivileged attacker use `public outbound init flows and inbound proof flows on every chain adapter` with control over recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms and desynchronize `near/omni-types recipient parsing via bridge entrypoints` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `one inbound event spawns multiple outbound obligations` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress`, violating `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended`?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Also assert cross-module consistency between `near/omni-types recipient parsing via bridge entrypoints` and the adjacent token-mapping and asset-identity logic after every branch.
