# Q663: NEAR cross-chain recipient parsing recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public outbound init flows and inbound proof flows on every chain adapter` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types recipient parsing via bridge entrypoints` violate `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended` in the `recipient or message ambiguity` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress` becomes fragile at those edges?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
