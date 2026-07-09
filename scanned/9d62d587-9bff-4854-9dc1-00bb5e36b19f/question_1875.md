# Q1875: NEAR OmniToken ft_resolve_transfer callback-bearing token flow exposes inconsistent intermediate state at boundary values

## Question
Can an unprivileged attacker trigger `fungible-token resolver reached after public `ft_transfer_call`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::ft_resolve_transfer` violate `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value` in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
