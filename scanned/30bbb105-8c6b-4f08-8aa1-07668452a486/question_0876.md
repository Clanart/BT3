# Q0876: WombatStaking.withdraw - fee split truncation drains the residual

## Question
Note that in wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked and force `totalAccumulated in mWOM` apart from `veWom balance of WombatStaking`, breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `totalAccumulated in mWOM` equals `veWom balance of WombatStaking` and that no account can withdraw more than it put in.
