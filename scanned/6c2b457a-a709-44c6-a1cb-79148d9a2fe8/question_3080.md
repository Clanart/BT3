# Q3080: NEAR OmniToken ft_resolve_transfer global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `fungible-token resolver reached after public `ft_transfer_call`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-token/src/lib.rs::ft_resolve_transfer` violate `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value` in the `global asset-conservation invariant break` attack class because resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
