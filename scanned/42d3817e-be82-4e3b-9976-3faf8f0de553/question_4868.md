# Q4868: WombatStaking.harvest - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol - _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the attacker deposits and withdraws through the same helper inside one transaction, exploit this through `harvest(address _lpToken)` to break the reconciliation between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` and the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under the attacker deposits and withdraws through the same helper inside one transaction, asserting at the end that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` still equals `_liquidity burned from the receipt token` and the PoC's balance delta is non-positive.
