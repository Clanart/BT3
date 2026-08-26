# Q1285: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
Consider wombat/ArbWomUp.sol, where the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Assuming userWOMDeposited is still zero for the caller, can an unprivileged attacker turn this into a divergence between `rewardAmount / DENOMINATOR` and `claimedReward[account]` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up userWOMDeposited is still zero for the caller, snapshot `rewardAmount / DENOMINATOR` and `claimedReward[account]`, run the attacker's `incentiveDeposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
