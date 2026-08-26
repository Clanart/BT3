# Q2090: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
wombat/WombatPoolHelperV2.sol - the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an unprivileged attacker controlling _lpAmount, under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` and the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting on every row that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
