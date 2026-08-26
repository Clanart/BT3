# Q3784: WombatStaking.deposit - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `feeInfos[i].value` unreconciled with `totalFee`, violates the invariant that harvested reward measurement must be isolated from the fee movements that consume it, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool is marked isPoolFeeFree so the fee loop is skipped entirely, snapshot `feeInfos[i].value` and `totalFee`, run the attacker's `deposit(address,uint256,uint256,address,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
