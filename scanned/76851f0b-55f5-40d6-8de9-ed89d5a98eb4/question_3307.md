# Q3307: WombatStaking.deposit - reward amounts measured by balance delta across the same fee loop

## Question
Consider wombat/WombatStaking.sol, where _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Assuming the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, have the attacker run `deposit(address,uint256,uint256,address,address) via a pool helper`, then assert the victim's claimable value and the `IERC20(wom).balanceOf(address(this))` versus `totalConverted in mWOM` relation are unchanged by the attacker's transaction.
