# Q4704: WombatStaking.withdraw - fee split truncation drains the residual

## Question
Consider wombat/WombatStaking.sol, where _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Assuming the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged attacker turn this into a divergence between `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the deposit token for the pool is wBNB and the helper arrived through depositNative, snapshot `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, run the attacker's `withdraw(address,uint256,uint256,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
