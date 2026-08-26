# Q1297: WombatStaking.harvest - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. With _lpToken and the timing of every harvest-driven fee split under attacker control and the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is holding WOM collected as a protocol fee that has not yet been split, then assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` end identical in both runs.
