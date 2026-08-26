# Q0325: BNBZapper.zapInToken - receiver is caller-supplied on a shared contract

## Question
rewards/BNBZapper.sol: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. With fromToken, amount, minRec and receiver, all unrestricted under attacker control and routePairAddresses is unset for the token so a direct two-hop path is used, can an unprivileged caller sequence `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` so that `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender` no longer reconcile, violating the invariant that the account whose tokens are spent must be the account that receives the proceeds and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: receiver is caller-supplied on a shared contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() forwards the caller's receiver into swapExactTokensForETH, so the destination of the proceeds is decoupled from the account whose tokens were pulled. Precondition: routePairAddresses is unset for the token so a direct two-hop path is used.
- Invariant to test: the account whose tokens are spent must be the account that receives the proceeds; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange routePairAddresses is unset for the token so a direct two-hop path is used, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `IERC20(fromToken).balanceOf(address(this))` equals `amount pulled from msg.sender` and that no account can withdraw more than it put in.
