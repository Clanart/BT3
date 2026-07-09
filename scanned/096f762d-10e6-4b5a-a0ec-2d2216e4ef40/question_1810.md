# Q1810: NEAR cross-chain recipient parsing address alias collapses distinct bridge subjects through cross-module drift

## Question
Can an unprivileged attacker use `public outbound init flows and inbound proof flows on every chain adapter` with control over recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms and desynchronize `near/omni-types recipient parsing via bridge entrypoints` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `address alias collapses distinct bridge subjects` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress`, violating `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended`?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Also assert cross-module consistency between `near/omni-types recipient parsing via bridge entrypoints` and the adjacent token-mapping and asset-identity logic after every branch.
