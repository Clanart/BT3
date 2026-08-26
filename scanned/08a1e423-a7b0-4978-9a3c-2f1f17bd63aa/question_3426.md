# Q3426: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
Consider wombat/WombatStaking.sol, where _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Assuming the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged attacker turn this into a divergence between `feeInfos[i].value` and `totalFee` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `feeInfos[i].value` versus `totalFee` relation are unchanged by the attacker's transaction.
