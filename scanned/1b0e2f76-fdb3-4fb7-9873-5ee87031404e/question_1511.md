# Q1511: WombatStaking.deposit - fee split truncation drains the residual

## Question
In wombat/WombatStaking.sol, _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an unprivileged attacker reach this through `deposit(address,uint256,uint256,address,address) via a pool helper` while the contract is holding WOM collected as a protocol fee that has not yet been split, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM collected as a protocol fee that has not yet been split, snapshot `totalAccumulated in mWOM` and `veWom balance of WombatStaking`, run the attacker's `deposit(address,uint256,uint256,address,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
