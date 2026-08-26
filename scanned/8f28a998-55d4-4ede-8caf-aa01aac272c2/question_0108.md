# Q0108: BNBZapper.zapInToken - route path derived from mutable owner state without validation

## Question
Note that in rewards/BNBZapper.sol, _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Can an attacker holding only tokens bought on market reach it via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` under the router leaves a non-zero allowance after the swap and force `previewAmount(token, amount)` apart from `the executed swap output`, breaking the invariant that a routing table entry must be validated against real liquidity before value is sent through it for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: route path derived from mutable owner state without validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a routing table entry must be validated against real liquidity before value is sent through it; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the router leaves a non-zero allowance after the swap, have the attacker run `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, then assert the victim's claimable value and the `previewAmount(token, amount)` versus `the executed swap output` relation are unchanged by the attacker's transaction.
