# Q2077: WomUp.stake - startTime is stored but never enforced on entry

## Question
Note that in wombat/WomUp.sol, the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Can an attacker holding only tokens bought on market reach it via `stake(uint256 _amount)` under the attacker migrates and withdraws inside one transaction and force `rewards[account]` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that an entry window that exists in state must be enforced on the entry path for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker migrates and withdraws inside one transaction, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `rewards[account]` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
