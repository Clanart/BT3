# Q1753: WomUp.stake - startTime is stored but never enforced on entry

## Question
Consider wombat/WomUp.sol, where the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Assuming the target helper leaves a non-zero allowance after depositFor, can an unprivileged attacker turn this into a divergence between `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` via `stake(uint256 _amount)`, breaking the invariant that an entry window that exists in state must be enforced on the entry path and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the target helper leaves a non-zero allowance after depositFor, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that an entry window that exists in state must be enforced on the entry path.
