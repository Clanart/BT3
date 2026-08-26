# Q0384: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
Consider wombat/WombatPoolHelperV2.sol, where the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `depositLP(uint256 _lpAmount)`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting on every row that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
