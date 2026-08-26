# Q4871: WombatPoolHelper.depositNative - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelper.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, and drive `IERC20(stakingToken).totalSupply()` out of agreement with `the MasterWombat staked balance for pid` - breaking the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `IERC20(stakingToken).totalSupply()` versus `the MasterWombat staked balance for pid` relation are unchanged by the attacker's transaction.
