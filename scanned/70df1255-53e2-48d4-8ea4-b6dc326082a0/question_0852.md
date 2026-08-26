# Q0852: BNBZapper.zapInToken - route path derived from mutable owner state without validation

## Question
Note that in rewards/BNBZapper.sol, _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Can an attacker holding only tokens bought on market reach it via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` under the caller names a token with a transfer hook they control and force `previewAmount(token, amount)` apart from `the executed swap output`, breaking the invariant that a routing table entry must be validated against real liquidity before value is sent through it for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: route path derived from mutable owner state without validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Precondition: the caller names a token with a transfer hook they control.
- Invariant to test: a routing table entry must be validated against real liquidity before value is sent through it; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under the caller names a token with a transfer hook they control, asserting at the end that `previewAmount(token, amount)` still equals `the executed swap output` and the PoC's balance delta is non-positive.
