# Q1394: WomUp.stake - startTime is stored but never enforced on entry

## Question
In wombat/WomUp.sol, the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Starting from a state where the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged EOA use `stake(uint256 _amount)` to leave `_totalSupply` inconsistent with `IERC20(mWom).balanceOf(address(this))`, violating the invariant that an entry window that exists in state must be enforced on the entry path and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM) under the reward period has just ended so periodFinish is behind block.timestamp, asserting on every row that an entry window that exists in state must be enforced on the entry path.
