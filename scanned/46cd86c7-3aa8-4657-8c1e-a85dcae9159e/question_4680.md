# Q4680: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while the deposit token for the pool is wBNB and the helper arrived through depositNative, and drive `IERC20(poolInfo.lpAddress).balanceOf(address(this))` out of agreement with `lpReceived credited by IMintableERC20(receiptToken).mint` - breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `withdraw(address,uint256,uint256,address) via a pool helper`: constrain the setup so that the deposit token for the pool is wBNB and the helper arrived through depositNative, fuzz the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw), and assert after every call that harvested reward measurement must be isolated from the fee movements that consume it.
