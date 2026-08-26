# Q5190: WombatPoolHelperV2.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
Note that in wombat/WombatPoolHelperV2.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under the attacker deposits and withdraws through the helper inside one transaction and force `_minimumLiquidity supplied by the caller` apart from `the LP actually minted by the Wombat pool`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker deposits and withdraws through the helper inside one transaction, have the attacker run `deposit(uint256 _amount, uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_minimumLiquidity supplied by the caller` versus `the LP actually minted by the Wombat pool` relation are unchanged by the attacker's transaction.
