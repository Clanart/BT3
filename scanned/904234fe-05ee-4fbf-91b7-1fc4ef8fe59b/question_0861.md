# Q0861: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
In wombat/WomUp.sol, stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Can an unprivileged attacker reach this through `getReward()` while the attacker funds the stake with a flash loan of WOM repaid in the same transaction, and drive `_totalSupply` out of agreement with `IERC20(mWom).balanceOf(address(this))` - breaking the invariant that every function that mutates the stake ledger must share one reentrancy domain - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `getReward()`: constrain the setup so that the attacker funds the stake with a flash loan of WOM repaid in the same transaction, fuzz the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller), and assert after every call that every function that mutates the stake ledger must share one reentrancy domain.
