# Q0790: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
Note that in rewards/BNBZapper.sol, fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Can an attacker holding only tokens bought on market reach it via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` under the caller names a token with a transfer hook they control and force `minRec supplied by the caller` apart from `amounts[amounts.length - 1] returned by the router`, breaking the invariant that a shared zapper must restrict which tokens it will pull, approve and route for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: the caller names a token with a transfer hook they control.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller names a token with a transfer hook they control, then assert `minRec supplied by the caller` and `amounts[amounts.length - 1] returned by the router` end identical in both runs.
