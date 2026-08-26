# Q5315: WombatPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/WombatPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Starting from a state where the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged EOA use `deposit(uint256 _amount, uint256 _minimumLiquidity)` to leave `_minimumLiquidity supplied by the caller` inconsistent with `the LP actually minted by the Wombat pool`, violating the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the attacker deposits and withdraws through the helper inside one transaction, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
