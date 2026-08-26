# Q4292: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
Note that in wombat/WombatPoolHelperV2.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body and force `IERC20(stakingToken).totalSupply()` apart from `the MasterWombat staked balance for pid`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `depositLP(uint256 _lpAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
