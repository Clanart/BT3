# Q2786: NEAR OmniToken ft_resolve_transfer global asset-conservation invariant break via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `fungible-token resolver reached after public `ft_transfer_call`` and then replay or reorder later callback or refund resolution so that `near/omni-token/src/lib.rs::ft_resolve_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `global asset-conservation invariant break` under resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
