# Q1829: NEAR resolve_fast_transfer fast path and normal path can both pay at boundary values

## Question
Can an unprivileged attacker trigger `callback after `send_tokens` in the fast Near path` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_fast_transfer` violate `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn` in the `fast path and normal path can both pay` attack class because burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
