# Q3927: WombatStaking.withdraw - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint` no longer reconcile, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool is marked isPoolFeeFree so the fee loop is skipped entirely, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `IERC20(poolInfo.lpAddress).balanceOf(address(this))` versus `lpReceived credited by IMintableERC20(receiptToken).mint` relation are unchanged by the attacker's transaction.
