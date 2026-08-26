# Q3896: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while the pool is marked isPoolFeeFree so the fee loop is skipped entirely, and drive `womRewards measured by balance delta` out of agreement with `the amount queued into poolInfo.rewarder` - breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool is marked isPoolFeeFree so the fee loop is skipped entirely, snapshot `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder`, run the attacker's `withdraw(address,uint256,uint256,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
