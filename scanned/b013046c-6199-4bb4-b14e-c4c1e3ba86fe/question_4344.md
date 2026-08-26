# Q4344: WombatPoolHelperV2.depositNative - receipt-token delta credited to an attacker-chosen beneficiary

## Question
Consider wombat/WombatPoolHelperV2.sol, where _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Assuming the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
