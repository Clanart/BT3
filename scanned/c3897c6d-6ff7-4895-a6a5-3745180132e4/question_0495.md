# Q495: NEAR cross-chain recipient parsing recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public outbound init flows and inbound proof flows on every chain adapter` with control over recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms and desynchronize `near/omni-types recipient parsing via bridge entrypoints` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress`, violating `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended`?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `near/omni-types recipient parsing via bridge entrypoints` and the adjacent token-mapping and asset-identity logic after every branch.
