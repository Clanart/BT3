# Q1327: NEAR cross-chain recipient parsing one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public outbound init flows and inbound proof flows on every chain adapter` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types recipient parsing via bridge entrypoints` violate `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended` in the `one inbound event spawns multiple outbound obligations` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress` becomes fragile at those edges?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
