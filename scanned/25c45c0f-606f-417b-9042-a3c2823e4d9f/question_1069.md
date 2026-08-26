# Q1069: BNBZapper.zapInToken - receiver is caller-supplied on a shared contract

## Question
rewards/BNBZapper.sol: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. With fromToken, amount, minRec and receiver, all unrestricted under attacker control and a residual balance of the token from an earlier zap sits on the contract, can an unprivileged caller sequence `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` so that `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender` no longer reconcile, violating the invariant that the account whose tokens are spent must be the account that receives the proceeds and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: receiver is caller-supplied on a shared contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: the account whose tokens are spent must be the account that receives the proceeds; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual balance of the token from an earlier zap sits on the contract, snapshot `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
