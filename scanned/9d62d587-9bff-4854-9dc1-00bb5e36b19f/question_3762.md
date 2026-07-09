# Q3762: NEAR OmniToken legacy ft_transfer migration swap leaves old and new claims live

## Question
Can an unprivileged attacker route value through `public token transfer entrypoint on wrapped Near tokens` so that `near/omni-token/src/lib.rs::ft_transfer` burns the old token but still leaves a live claim on the old path while minting the new token, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims.
