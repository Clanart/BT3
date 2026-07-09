# Q3039: NEAR resolve_fast_transfer relayer substitution changes economic recipient at boundary values

## Question
Can an unprivileged attacker trigger `callback after `send_tokens` in the fast Near path` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_fast_transfer` violate `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn` in the `relayer substitution changes economic recipient` attack class because burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
