# Q5170: AnkrBNBPoolHelper.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/AnkrBNBPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Starting from a state where the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged EOA use `depositLP(uint256 _lpAmount)` to leave `pid cached at construction` inconsistent with `pools[lpToken].pid in WombatStaking`, violating the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the attacker deposits and withdraws through the helper inside one transaction, asserting on every row that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
