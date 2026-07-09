# Q3078: NEAR OmniToken legacy ft_transfer numeric cast or overflow changes economic meaning at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer entrypoint on wrapped Near tokens` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-token/src/lib.rs::ft_transfer` violate `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path` in the `numeric cast or overflow changes economic meaning` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
