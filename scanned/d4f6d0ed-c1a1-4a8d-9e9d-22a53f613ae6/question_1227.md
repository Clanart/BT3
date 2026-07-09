# Q1227: NEAR OmniToken legacy ft_transfer recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public token transfer entrypoint on wrapped Near tokens` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-token/src/lib.rs::ft_transfer` violate `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path` in the `recipient or message ambiguity` attack class because contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver becomes fragile at those edges?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
