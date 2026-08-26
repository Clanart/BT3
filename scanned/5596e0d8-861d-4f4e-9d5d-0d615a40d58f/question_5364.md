# Q5364: WombatPoolHelper.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/WombatPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under the attacker deposits and withdraws through the helper inside one transaction, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under the attacker deposits and withdraws through the helper inside one transaction, asserting at the end that `pid cached at construction` still equals `pools[lpToken].pid in WombatStaking` and the PoC's balance delta is non-positive.
