# Q394: NEAR OmniToken legacy ft_transfer burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and the presence of a configured withdraw relayer address and desynchronize `near/omni-token/src/lib.rs::ft_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer` and the adjacent mint, burn, or custody accounting after every branch.
