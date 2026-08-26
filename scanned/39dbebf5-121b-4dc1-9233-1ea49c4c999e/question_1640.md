# Q1640: WombatStaking.withdraw - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while the contract is holding WOM collected as a protocol fee that has not yet been split, and drive `IMintableERC20(poolInfo.receiptToken).totalSupply()` out of agreement with `IMasterWombat(masterWombat) staked balance for poolInfo.pid` - breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM collected as a protocol fee that has not yet been split, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and that no account can withdraw more than it put in.
