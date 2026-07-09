# Q2637: NEAR OmniToken legacy ft_transfer numeric cast or overflow changes economic meaning

## Question
Can an unprivileged attacker use `public token transfer entrypoint on wrapped Near tokens` to push `near/omni-token/src/lib.rs::ft_transfer` through a cast, overflow, or truncation path that changes amount or nonce semantics, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations.
