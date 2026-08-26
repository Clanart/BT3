# Q3341: WombatStaking.deposit - fee split truncation drains the residual

## Question
Note that in wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an attacker holding only tokens bought on market reach it via `deposit(address,uint256,uint256,address,address) via a pool helper` under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction and force `womRewards measured by balance delta` apart from `the amount queued into poolInfo.rewarder`, breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, asserting on every row that every harvested unit must end up either in a fee destination or in the pool rewarder.
