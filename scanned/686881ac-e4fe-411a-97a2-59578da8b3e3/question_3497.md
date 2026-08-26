# Q3497: WombatPoolHelper.depositNative - receipt-token delta credited to an attacker-chosen beneficiary

## Question
Consider wombat/WombatPoolHelper.sol, where _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Assuming a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual stakingToken balance from an earlier rounding sits on the helper, snapshot `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
