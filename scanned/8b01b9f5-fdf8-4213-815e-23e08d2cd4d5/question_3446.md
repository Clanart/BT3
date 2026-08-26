# Q3446: WombatPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
wombat/WombatPoolHelper.sol: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. With _lpAmount and the LP tokens pulled from the caller under attacker control and a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` no longer reconcile, violating the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that a residual stakingToken balance from an earlier rounding sits on the helper, fuzz the attacker inputs (_lpAmount and the LP tokens pulled from the caller), and assert after every call that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
