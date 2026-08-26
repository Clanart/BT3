# Q1079: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
wombat/ArbWomUp.sol - the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller splits the same total deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `claimedReward[account]` and `userWOMDeposited[account]` and the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the caller splits the same total deposit across several addresses.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller splits the same total deposit across several addresses, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
