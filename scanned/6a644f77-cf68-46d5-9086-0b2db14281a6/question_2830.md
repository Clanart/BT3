# Q2830: NEAR UTXO other-chain forwarder fast path changes fee semantics without changing proof identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO branch reached through `ft_on_transfer`` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path changes fee semantics without changing proof identity` under turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
