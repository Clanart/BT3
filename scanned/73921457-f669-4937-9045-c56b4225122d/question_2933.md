# Q2933: NEAR OmniToken ft_resolve_transfer global asset-conservation invariant break through cross-module drift

## Question
Can an unprivileged attacker use `fungible-token resolver reached after public `ft_transfer_call`` with control over sender, receiver, amount, and the receiver-returned used amount and desynchronize `near/omni-token/src/lib.rs::ft_resolve_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `global asset-conservation invariant break` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_resolve_transfer` and the adjacent mint, burn, or custody accounting after every branch.
