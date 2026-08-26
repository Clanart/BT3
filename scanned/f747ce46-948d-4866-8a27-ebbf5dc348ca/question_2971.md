# Q2971: WomUp.stake - startTime is stored but never enforced on entry

## Question
wombat/WomUp.sol - the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Can an unprivileged attacker controlling _amount and the block, with the WOM immediately converted 1:1 into mWOM, under the attacker stakes one wei so _totalSupply is non-zero but every division truncates, exploit this through `stake(uint256 _amount)` to break the reconciliation between `_balances[account]` and `_totalSupply` and the invariant that an entry window that exists in state must be enforced on the entry path, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM) under the attacker stakes one wei so _totalSupply is non-zero but every division truncates, asserting on every row that an entry window that exists in state must be enforced on the entry path.
