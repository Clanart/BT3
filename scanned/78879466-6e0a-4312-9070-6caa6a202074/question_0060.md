# Q60: NEAR OmniToken ft_resolve_transfer mint-with-message path differs economically from plain mint

## Question
Can an unprivileged attacker trigger `fungible-token resolver reached after public `ft_transfer_call`` so that `near/omni-token/src/lib.rs::ft_resolve_transfer` mints through a callback-bearing path whose failure semantics differ from plain minting, violating `resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_resolve_transfer`
- Entrypoint: `fungible-token resolver reached after public `ft_transfer_call``
- Attacker controls: sender, receiver, amount, and the receiver-returned used amount
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks.
- Invariant to test: resolution must not let bridge flows misread used-versus-refunded amounts and therefore burn, mint, or log the wrong cross-chain value
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches.
