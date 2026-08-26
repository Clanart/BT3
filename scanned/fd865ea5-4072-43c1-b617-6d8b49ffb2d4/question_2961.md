# Q2961: WombatStaking.withdraw - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Starting from a state where smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged EOA use `withdraw(address,uint256,uint256,address) via a pool helper` to leave `womRewards measured by balance delta` inconsistent with `the amount queued into poolInfo.rewarder`, violating the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
