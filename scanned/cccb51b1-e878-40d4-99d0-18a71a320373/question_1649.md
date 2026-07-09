# Q1649: NEAR cross-chain recipient parsing address alias collapses distinct bridge subjects via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public outbound init flows and inbound proof flows on every chain adapter` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-types recipient parsing via bridge entrypoints` ends up accepting two inconsistent interpretations of the same economic event specifically around `address alias collapses distinct bridge subjects` under recipient data is user-controlled on outbound flows and proof-controlled on inbound flows before conversion into `OmniAddress`, violating `recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended`?

## Target
- File/function: `near/omni-types recipient parsing via bridge entrypoints`
- Entrypoint: `public outbound init flows and inbound proof flows on every chain adapter`
- Attacker controls: recipient strings, byte arrays, account ids, and mixed-case hex or base58 forms
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: recipient parsing must not admit alternate textual encodings that resolve to a different chain or address than the one users and proofs intended
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
