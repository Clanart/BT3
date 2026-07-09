# Q2931: NEAR OmniToken legacy ft_transfer numeric cast or overflow changes economic meaning through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and the presence of a configured withdraw relayer address and desynchronize `near/omni-token/src/lib.rs::ft_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `numeric cast or overflow changes economic meaning` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer` and the adjacent mint, burn, or custody accounting after every branch.
