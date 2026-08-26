# Q0411: WombatStaking.harvest - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `harvest(address _lpToken)` to leave `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` inconsistent with `_liquidity burned from the receipt token`, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` versus `_liquidity burned from the receipt token` relation are unchanged by the attacker's transaction.
