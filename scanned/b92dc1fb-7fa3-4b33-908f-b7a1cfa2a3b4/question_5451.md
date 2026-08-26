# Q5451: WombatStaking.withdraw - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Does `withdraw(address,uint256,uint256,address) via a pool helper` let an unprivileged caller exploit that under the bonus reward token registered for the asset is also one of the fee currencies, so that `feeInfos[i].value` diverges from `totalFee`, the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bonus reward token registered for the asset is also one of the fee currencies, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `feeInfos[i].value` equals `totalFee` and that no account can withdraw more than it put in.
