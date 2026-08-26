# Q1654: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. With the exact block at which accrued MGP is locked into vlMGP for the caller under attacker control and the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged caller sequence `getReward()` so that `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that every function that mutates the stake ledger must share one reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller) under the reward period has just ended so periodFinish is behind block.timestamp, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
