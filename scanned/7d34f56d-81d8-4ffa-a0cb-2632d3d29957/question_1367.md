# Q1367: WomUp.stake - no reentrancy guard on any balance-mutating function

## Question
wombat/WomUp.sol: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Under the reward period has just ended so periodFinish is behind block.timestamp, is there an unprivileged sequence of `stake(uint256 _amount)` that leaves `_balances[account]` unreconciled with `_totalSupply`, violates the invariant that every function that mutates the stake ledger must share one reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: no reentrancy guard on any balance-mutating function)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake, withdraw, migrate and getReward all carry only the updateReward modifier, with no nonReentrant, while performing external token transfers, deposits and lock calls. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: every function that mutates the stake ledger must share one reentrancy domain; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM) under the reward period has just ended so periodFinish is behind block.timestamp, asserting on every row that every function that mutates the stake ledger must share one reentrancy domain.
