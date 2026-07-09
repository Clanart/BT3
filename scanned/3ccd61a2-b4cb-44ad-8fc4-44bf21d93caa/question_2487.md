# Q2487: NEAR OmniToken legacy ft_transfer legacy or migration path aliasing at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer entrypoint on wrapped Near tokens` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-token/src/lib.rs::ft_transfer` violate `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path` in the `legacy or migration path aliasing` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
