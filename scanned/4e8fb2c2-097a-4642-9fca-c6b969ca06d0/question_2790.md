# Q2790: WombatStaking.deposit - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Does `deposit(address,uint256,uint256,address,address) via a pool helper` let an unprivileged caller exploit that under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, so that `totalAccumulated in mWOM` diverges from `veWom balance of WombatStaking`, the invariant that harvested reward measurement must be isolated from the fee movements that consume it is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, have the attacker run `deposit(address,uint256,uint256,address,address) via a pool helper`, then assert the victim's claimable value and the `totalAccumulated in mWOM` versus `veWom balance of WombatStaking` relation are unchanged by the attacker's transaction.
