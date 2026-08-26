# Q0551: WomUp.stake - startTime is stored but never enforced on entry

## Question
In wombat/WomUp.sol, the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Can an unprivileged attacker reach this through `stake(uint256 _amount)` while the attacker funds the stake with a flash loan of WOM repaid in the same transaction, and drive `rewardRate * duration` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that an entry window that exists in state must be enforced on the entry path - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker funds the stake with a flash loan of WOM repaid in the same transaction, call `stake(uint256 _amount)`, and assert `rewardRate * duration` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
