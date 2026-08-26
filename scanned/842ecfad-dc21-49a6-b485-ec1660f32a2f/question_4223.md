# Q4223: WombatStaking.deposit - reward amounts measured by balance delta across the same fee loop

## Question
Note that in wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an attacker holding only tokens bought on market reach it via `deposit(address,uint256,uint256,address,address) via a pool helper` under several feeInfos entries are active at once and the harvested amount is small and force `womRewards measured by balance delta` apart from `the amount queued into poolInfo.rewarder`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that harvested reward measurement must be isolated from the fee movements that consume it.
