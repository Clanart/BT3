# Q4839: WombatPoolHelperV2.withdraw - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelperV2.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, so that `_minimumLiquidity supplied by the caller` diverges from `the LP actually minted by the Wombat pool`, the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, have the attacker run `withdraw(uint256 _liquidity, uint256 _minAmount)`, then assert the victim's claimable value and the `_minimumLiquidity supplied by the caller` versus `the LP actually minted by the Wombat pool` relation are unchanged by the attacker's transaction.
