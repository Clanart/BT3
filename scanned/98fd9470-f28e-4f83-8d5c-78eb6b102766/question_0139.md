# Q0139: BNBZapper.zapInToken - receiver is caller-supplied on a shared contract

## Question
rewards/BNBZapper.sol: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Under the router leaves a non-zero allowance after the swap, is there an unprivileged sequence of `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` that leaves `minRec supplied by the caller` unreconciled with `amounts[amounts.length - 1] returned by the router`, violates the invariant that the account whose tokens are spent must be the account that receives the proceeds, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: receiver is caller-supplied on a shared contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the account whose tokens are spent must be the account that receives the proceeds; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the router leaves a non-zero allowance after the swap, then assert `minRec supplied by the caller` and `amounts[amounts.length - 1] returned by the router` end identical in both runs.
