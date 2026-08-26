# Q0976: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
In rewards/BNBZapper.sol, fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while a residual balance of the token from an earlier zap sits on the contract, and drive `IERC20(fromToken).balanceOf(address(this))` out of agreement with `amount pulled from msg.sender` - breaking the invariant that a shared zapper must restrict which tokens it will pull, approve and route - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual balance of the token from an earlier zap sits on the contract, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `IERC20(fromToken).balanceOf(address(this))` equals `amount pulled from msg.sender` and that no account can withdraw more than it put in.
