# Q0511: BNBZapper.zapInToken - receiver is caller-supplied on a shared contract

## Question
In rewards/BNBZapper.sol, zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Starting from a state where routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged EOA use `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` to leave `previewAmount(token, amount)` inconsistent with `the executed swap output`, violating the invariant that the account whose tokens are spent must be the account that receives the proceeds and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: receiver is caller-supplied on a shared contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: the account whose tokens are spent must be the account that receives the proceeds; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted) under routePairAddresses points at a pair with no meaningful liquidity, asserting on every row that the account whose tokens are spent must be the account that receives the proceeds.
