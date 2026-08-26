# Q0232: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
In rewards/BNBZapper.sol, fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while routePairAddresses is unset for the token so a direct two-hop path is used, and drive `IERC20(fromToken).balanceOf(address(this))` out of agreement with `amount pulled from msg.sender` - breaking the invariant that a shared zapper must restrict which tokens it will pull, approve and route - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: routePairAddresses is unset for the token so a direct two-hop path is used.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`: constrain the setup so that routePairAddresses is unset for the token so a direct two-hop path is used, fuzz the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted), and assert after every call that a shared zapper must restrict which tokens it will pull, approve and route.
