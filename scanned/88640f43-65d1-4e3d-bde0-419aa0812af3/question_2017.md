# Q2017: WombatStaking.harvest - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. With _lpToken and the timing of every harvest-driven fee split under attacker control and a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged caller sequence `harvest(address _lpToken)` so that `totalAccumulated in mWOM` and `veWom balance of WombatStaking` no longer reconcile, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, then assert `totalAccumulated in mWOM` and `veWom balance of WombatStaking` end identical in both runs.
