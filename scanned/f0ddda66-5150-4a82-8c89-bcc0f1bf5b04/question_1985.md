# Q1985: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol - stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker controlling the exact block at which accrued MGP is locked into vlMGP for the caller, under the target helper leaves a non-zero allowance after depositFor, exploit this through `getReward()` to break the reconciliation between `lastUpdateTime` and `periodFinish` and the invariant that every function that mutates the stake ledger must share one reentrancy domain, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `getReward()`: constrain the setup so that the target helper leaves a non-zero allowance after depositFor, fuzz the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller), and assert after every call that every function that mutates the stake ledger must share one reentrancy domain.
