# Q3888: NEAR OmniToken legacy ft_transfer migration swap leaves old and new claims live via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public token transfer entrypoint on wrapped Near tokens` and then replay or reorder the later settlement leg on another chain so that `near/omni-token/src/lib.rs::ft_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `migration swap leaves old and new claims live` under contains a legacy branch that reroutes self-transfers with `WITHDRAW_TO:` memos to the stored withdraw relayer instead of the nominal receiver, violating `legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path`?

## Target
- File/function: `near/omni-token/src/lib.rs::ft_transfer`
- Entrypoint: `public token transfer entrypoint on wrapped Near tokens`
- Attacker controls: receiver id, amount, memo, and the presence of a configured withdraw relayer address
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy withdrawal shortcuts must never let arbitrary token holders create bridge withdrawals or third-party transfers that bypass the intended bridge accounting path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
