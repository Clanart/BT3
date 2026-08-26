# Q5530: WombatPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
Consider wombat/WombatPoolHelper.sol, where the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Assuming the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `deposit(uint256 _amount, uint256 _minimumLiquidity)`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the receipt token is minted to the helper while the credit is directed at a different address, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
