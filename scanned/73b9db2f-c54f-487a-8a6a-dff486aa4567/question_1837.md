# Q1837: WombatPoolHelperV2.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/WombatPoolHelperV2.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, so that `IERC20(stakingToken).totalSupply()` diverges from `the MasterWombat staked balance for pid`, the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, have the attacker run `deposit(uint256 _amount, uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `IERC20(stakingToken).totalSupply()` versus `the MasterWombat staked balance for pid` relation are unchanged by the attacker's transaction.
