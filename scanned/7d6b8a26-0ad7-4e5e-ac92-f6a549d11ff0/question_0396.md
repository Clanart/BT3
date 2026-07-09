# Q396: NEAR OmniToken ft_resolve_transfer mint-with-message path differs economically from plain mint through cross-module drift

## Question
Can an unprivileged attacker use `fungible-token resolver reached after public `ft_transfer_call`` with control over sender, receiver, amount, and the receiver-returned used amount and desynchronize `near/omni-token/src/lib.rs::ft_resolve_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `mint-with-message path differs economically from plain mint` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_resolve_transfer` and the adjacent mint, burn, or custody accounting after every branch.
