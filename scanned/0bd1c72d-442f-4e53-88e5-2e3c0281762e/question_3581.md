# Q3581: AnkrBNBPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/AnkrBNBPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, so that `IERC20(stakingToken).balanceOf(address(this)) delta` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, fuzz the attacker inputs (_amount and _minimumLiquidity), and assert after every call that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
