# Q3688: WombatStaking.harvest - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while the pool is marked isPoolFeeFree so the fee loop is skipped entirely, and drive `womRewards measured by balance delta` out of agreement with `the amount queued into poolInfo.rewarder` - breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, asserting at the end that `womRewards measured by balance delta` still equals `the amount queued into poolInfo.rewarder` and the PoC's balance delta is non-positive.
