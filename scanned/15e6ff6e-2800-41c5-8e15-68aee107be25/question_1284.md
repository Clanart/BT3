# Q1284: WomUp.getReward - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Under _totalSupply exceeds the mWOM balance the contract actually holds, is there an unprivileged sequence of `getReward()` that leaves `rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[account]`, violates the invariant that every function that mutates the stake ledger must share one reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `getReward()` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which accrued MGP is locked into vlMGP for the caller
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which accrued MGP is locked into vlMGP for the caller) under _totalSupply exceeds the mWOM balance the contract actually holds, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
