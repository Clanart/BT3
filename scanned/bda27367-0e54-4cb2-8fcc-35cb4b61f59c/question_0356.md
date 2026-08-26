# Q0356: BNBZapper.zapInToken - no per-caller accounting at all

## Question
rewards/BNBZapper.sol: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. With fromToken, amount, minRec and receiver, all unrestricted under attacker control and routePairAddresses is unset for the token so a direct two-hop path is used, can an unprivileged caller sequence `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` so that `previewAmount(token, amount)` and `the executed swap output` no longer reconcile, violating the invariant that a contract that holds user value even transiently must attribute it per account and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: no per-caller accounting at all)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Precondition: routePairAddresses is unset for the token so a direct two-hop path is used.
- Invariant to test: a contract that holds user value even transiently must attribute it per account; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish routePairAddresses is unset for the token so a direct two-hop path is used, have the attacker run `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, then assert the victim's claimable value and the `previewAmount(token, amount)` versus `the executed swap output` relation are unchanged by the attacker's transaction.
