# Q5439: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Under the bonus reward token registered for the asset is also one of the fee currencies, is there an unprivileged sequence of `withdraw(address,uint256,uint256,address) via a pool helper` that leaves `totalAccumulated in mWOM` unreconciled with `veWom balance of WombatStaking`, violates the invariant that harvested reward measurement must be isolated from the fee movements that consume it, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bonus reward token registered for the asset is also one of the fee currencies, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `totalAccumulated in mWOM` equals `veWom balance of WombatStaking` and that no account can withdraw more than it put in.
