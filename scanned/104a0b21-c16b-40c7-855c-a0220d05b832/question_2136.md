# Q2136: WombatPoolHelperV2.depositLP - no reentrancy guard anywhere on the helper

## Question
wombat/WombatPoolHelperV2.sol - none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker controlling _lpAmount, under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting on every row that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
