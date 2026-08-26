# Q2702: WomUp.stake - startTime is stored but never enforced on entry

## Question
wombat/WomUp.sol: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Under the attacker calls getReward immediately after a large stake by another user, is there an unprivileged sequence of `stake(uint256 _amount)` that leaves `rewardRate * duration` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that an entry window that exists in state must be enforced on the entry path, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the attacker calls getReward immediately after a large stake by another user, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that an entry window that exists in state must be enforced on the entry path.
