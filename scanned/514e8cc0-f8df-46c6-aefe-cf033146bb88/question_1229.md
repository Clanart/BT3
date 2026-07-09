# Q1229: NEAR OmniToken ft_resolve_transfer custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `fungible-token resolver reached after public `ft_transfer_call`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::ft_resolve_transfer` violate `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value` in the `custody accounting diverges from wrapped supply` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
