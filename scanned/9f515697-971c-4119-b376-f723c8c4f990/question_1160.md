# Q1160: BNBZapper.zapInToken - arbitrary fromToken with an attacker-controlled transfer hook

## Question
In rewards/BNBZapper.sol, fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, and drive `previewAmount(token, amount)` out of agreement with `the executed swap output` - breaking the invariant that a shared zapper must restrict which tokens it will pull, approve and route - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: arbitrary fromToken with an attacker-controlled transfer hook)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: fromToken is entirely caller-supplied and is used for safeTransferFrom, safeApprove and the swap path with no allowlist, so a caller can point the contract at a token whose transfer logic they wrote. Precondition: WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block.
- Invariant to test: a shared zapper must restrict which tokens it will pull, approve and route; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, asserting at the end that `previewAmount(token, amount)` still equals `the executed swap output` and the PoC's balance delta is non-positive.
