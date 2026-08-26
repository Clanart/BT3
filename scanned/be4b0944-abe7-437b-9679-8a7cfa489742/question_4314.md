# Q4314: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
Consider wombat/WombatStaking.sol, where _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Assuming several feeInfos entries are active at once and the harvested amount is small, can an unprivileged attacker turn this into a divergence between `isPoolFeeFree[_lpToken]` and `feeInfos.length` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that harvested reward measurement must be isolated from the fee movements that consume it.
