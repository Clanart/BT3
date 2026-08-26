# Q0697: BNBZapper.zapInToken - receiver is caller-supplied on a shared contract

## Question
In rewards/BNBZapper.sol, zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Does `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` let an unprivileged caller exploit that under the caller sets minRec to zero and sandwiches the PancakeSwap pair, so that `routePairAddresses[token]` diverges from `the path built by _findRouteToBnb`, the invariant that the account whose tokens are spent must be the account that receives the proceeds is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: receiver is caller-supplied on a shared contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Precondition: the caller sets minRec to zero and sandwiches the PancakeSwap pair.
- Invariant to test: the account whose tokens are spent must be the account that receives the proceeds; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under the caller sets minRec to zero and sandwiches the PancakeSwap pair, asserting at the end that `routePairAddresses[token]` still equals `the path built by _findRouteToBnb` and the PoC's balance delta is non-positive.
