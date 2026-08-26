# Q3120: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
In wombat/WombatStaking.sol, harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Starting from a state where the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged EOA use `harvest(address _lpToken)` to leave `IERC20(poolInfo.lpAddress).balanceOf(address(this))` inconsistent with `lpReceived credited by IMintableERC20(receiptToken).mint`, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, asserting at the end that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` still equals `lpReceived credited by IMintableERC20(receiptToken).mint` and the PoC's balance delta is non-positive.
