# Q0117: WomUp.stake - startTime is stored but never enforced on entry

## Question
Note that in wombat/WomUp.sol, the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Can an attacker holding only tokens bought on market reach it via `stake(uint256 _amount)` under the attacker is the only staker for a single block and force `lastUpdateTime` apart from `periodFinish`, breaking the invariant that an entry window that exists in state must be enforced on the entry path for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: startTime is stored but never enforced on entry)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: the contract keeps a startTime state variable while stake() only rejects a zero amount, so participation before the intended window is not prevented and reward accrual begins from whatever lastUpdateTime holds. Precondition: the attacker is the only staker for a single block.
- Invariant to test: an entry window that exists in state must be enforced on the entry path; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only staker for a single block, snapshot `lastUpdateTime` and `periodFinish`, run the attacker's `stake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
