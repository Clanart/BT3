# Q0645: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
Consider wombat/ArbWomUp.sol, where the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `rewardTier[i]` and `rewardMultiplier[i-1]` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sizes _amount to cross several tier boundaries at once, call `incentiveDeposit(uint256 _amount)`, and assert `rewardTier[i]` equals `rewardMultiplier[i-1]` and that no account can withdraw more than it put in.
