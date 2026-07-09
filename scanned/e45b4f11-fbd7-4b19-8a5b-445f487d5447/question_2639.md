# Q2639: NEAR OmniToken ft_resolve_transfer global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `fungible-token resolver reached after public `ft_transfer_call`` with the code paths summarized by `near/omni-token/src/lib.rs::ft_resolve_transfer` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by resolves `ft_transfer_call` by reconciling the actually-used amount with the original transfer, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
