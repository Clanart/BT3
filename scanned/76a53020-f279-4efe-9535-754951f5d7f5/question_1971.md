# Q1971: NEAR cross-chain recipient parsing address alias collapses distinct bridge subjects at boundary values

## Question
Can an unprivileged attacker trigger `public outbound init flows and inbound proof flows on every chain adapter` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types recipient parsing via bridge entrypoints` violate `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended` in the `address alias collapses distinct bridge subjects` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress` becomes fragile at those edges?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
