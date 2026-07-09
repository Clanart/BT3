# Q1714: NEAR OmniToken ft_resolve_transfer callback-bearing token flow exposes inconsistent intermediate state through cross-module drift

## Question
Can an unprivileged attacker use `fungible-token resolver reached after public `ft_transfer_call`` with control over sender, receiver, amount, and the receiver-returned used amount and desynchronize `near/omni-token/src/lib.rs::ft_resolve_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_resolve_transfer` and the adjacent mint, burn, or custody accounting after every branch.
