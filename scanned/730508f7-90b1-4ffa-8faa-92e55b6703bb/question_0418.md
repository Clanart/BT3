# Q0418: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
Consider rewards/BNBZapper.sol, where fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Assuming routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged attacker turn this into a divergence between `previewAmount(token, amount)` and `the executed swap output` via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, breaking the invariant that a shared zapper must restrict which tokens it will pull, approve and route and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up routePairAddresses points at a pair with no meaningful liquidity, snapshot `previewAmount(token, amount)` and `the executed swap output`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
