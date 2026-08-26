# Q0211: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
In wombat/ArbWomUp.sol, the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `rewardAmount / DENOMINATOR` out of agreement with `claimedReward[account]` - breaking the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated) under the contract's USDT balance is below the tier reward the deposit earned, asserting on every row that a one-way deposit must be matched by an entitlement that cannot be under-recorded.
