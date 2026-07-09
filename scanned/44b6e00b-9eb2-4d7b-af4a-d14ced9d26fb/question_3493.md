# Q3493: NEAR OmniToken legacy ft_transfer legacy withdrawal shortcut aliases a normal transfer through cross-module drift

## Question
Can an unprivileged attacker use `public token transfer entrypoint on wrapped Near tokens` with control over receiver id, amount, memo, and the presence of a configured withdraw relayer address and desynchronize `near/omni-token/src/lib.rs::ft_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `legacy withdrawal shortcut aliases a normal transfer` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Also assert cross-module consistency between `near/omni-token/src/lib.rs::ft_transfer` and the adjacent mint, burn, or custody accounting after every branch.
