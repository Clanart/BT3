# Q4137: WombatStaking.harvest - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. With _lpToken and the timing of every harvest-driven fee split under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `harvest(address _lpToken)` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that every harvested unit must end up either in a fee destination or in the pool rewarder.
