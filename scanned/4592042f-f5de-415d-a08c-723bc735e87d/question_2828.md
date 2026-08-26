# Q2828: WombatStaking.deposit - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Does `deposit(address,uint256,uint256,address,address) via a pool helper` let an unprivileged caller exploit that under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, so that `feeInfos[i].value` diverges from `totalFee`, the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, then assert `feeInfos[i].value` and `totalFee` end identical in both runs.
