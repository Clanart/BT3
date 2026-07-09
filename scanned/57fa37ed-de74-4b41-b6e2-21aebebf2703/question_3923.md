# Q3923: NEAR UTXO other-chain forwarder fast-transfer status changes in the wrong order via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO branch reached through `ft_on_transfer`` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer status changes in the wrong order` under turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
