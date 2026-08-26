# Q4340: WombatStaking.withdraw - fee split truncation drains the residual

## Question
Note that in wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under several feeInfos entries are active at once and the harvested amount is small and force `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` apart from `_liquidity burned from the receipt token`, breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that every harvested unit must end up either in a fee destination or in the pool rewarder.
