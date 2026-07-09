# Q327: NEAR cross-chain recipient parsing recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public outbound init flows and inbound proof flows on every chain adapter` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-types recipient parsing via bridge entrypoints` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress`, violating `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended`?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
