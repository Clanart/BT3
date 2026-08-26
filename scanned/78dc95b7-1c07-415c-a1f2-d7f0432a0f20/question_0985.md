# Q0985: WomUp.stake - startTime is stored but never enforced on entry

## Question
Consider wombat/WomUp.sol, where the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Assuming _totalSupply exceeds the mWOM balance the contract actually holds, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `_totalSupply` via `stake(uint256 _amount)`, breaking the invariant that an entry window that exists in state must be enforced on the entry path and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that _totalSupply exceeds the mWOM balance the contract actually holds, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that an entry window that exists in state must be enforced on the entry path.
