# Q4596: WombatStaking.deposit - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Starting from a state where the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged EOA use `deposit(address,uint256,uint256,address,address) via a pool helper` to leave `isPoolFeeFree[_lpToken]` inconsistent with `feeInfos.length`, violating the invariant that harvested reward measurement must be isolated from the fee movements that consume it and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `deposit(address,uint256,uint256,address,address) via a pool helper`: constrain the setup so that the deposit token for the pool is wBNB and the helper arrived through depositNative, fuzz the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper), and assert after every call that harvested reward measurement must be isolated from the fee movements that consume it.
