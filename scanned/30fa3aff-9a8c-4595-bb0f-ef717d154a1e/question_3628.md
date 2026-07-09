# Q3628: NEAR OmniToken legacy ft_transfer legacy withdrawal shortcut aliases a normal transfer at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer entrypoint on wrapped Near tokens` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-token/src/lib.rs::ft_transfer` violate `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path` in the `legacy withdrawal shortcut aliases a normal transfer` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Target memo-based or self-transfer-based legacy shortcuts in token wrappers. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise equivalent economic transfers with and without legacy markers and assert that only the intended path creates a bridge event. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
