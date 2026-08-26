# Q0604: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
rewards/BNBZapper.sol - fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Can an unprivileged attacker controlling fromToken, amount, minRec and receiver, all unrestricted, under the caller sets minRec to zero and sandwiches the PancakeSwap pair, exploit this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` to break the reconciliation between `routePairAddresses[token]` and `the path built by _findRouteToBnb` and the invariant that a shared zapper must restrict which tokens it will pull, approve and route, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: the caller sets minRec to zero and sandwiches the PancakeSwap pair.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets minRec to zero and sandwiches the PancakeSwap pair, have the attacker run `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, then assert the victim's claimable value and the `routePairAddresses[token]` versus `the path built by _findRouteToBnb` relation are unchanged by the attacker's transaction.
