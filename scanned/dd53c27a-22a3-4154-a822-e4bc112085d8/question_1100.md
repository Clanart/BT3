# Q1100: BNBZapper.zapInToken - no per-caller accounting at all

## Question
rewards/BNBZapper.sol: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. With fromToken, amount, minRec and receiver, all unrestricted under attacker control and a residual balance of the token from an earlier zap sits on the contract, can an unprivileged caller sequence `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` so that `previewAmount(token, amount)` and `the executed swap output` no longer reconcile, violating the invariant that a contract that holds user value even transiently must attribute it per account and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: no per-caller accounting at all)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: a contract that holds user value even transiently must attribute it per account; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under a residual balance of the token from an earlier zap sits on the contract, asserting at the end that `previewAmount(token, amount)` still equals `the executed swap output` and the PoC's balance delta is non-positive.
