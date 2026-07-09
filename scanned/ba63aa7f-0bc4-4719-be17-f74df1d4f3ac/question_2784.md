# Q2784: NEAR OmniToken legacy ft_transfer numeric cast or overflow changes economic meaning via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public token transfer entrypoint on wrapped Near tokens` and then replay or reorder the later settlement leg on another chain so that `near/omni-token/src/lib.rs::ft_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `numeric cast or overflow changes economic meaning` under contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
