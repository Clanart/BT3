# Q5535: WombatStaking.harvest - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Under the veWOM contract leaves a non-zero allowance after mint, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted in mWOM`, violates the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the veWOM contract leaves a non-zero allowance after mint, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that every harvested unit must end up either in a fee destination or in the pool rewarder.
