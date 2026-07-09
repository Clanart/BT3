# Q1346: NEAR resolve_fast_transfer fast path and normal path can both pay

## Question
Can an unprivileged attacker use `callback after `send_tokens` in the fast Near path` to make the fast path and the eventual normal settlement each believe they are the sole payer because `near/omni-bridge/src/lib.rs::resolve_fast_transfer` relies on burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split.
