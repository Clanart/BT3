# Q5385: WombatPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
wombat/WombatPoolHelper.sol - none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker controlling _lpAmount and the LP tokens pulled from the caller, under the attacker deposits and withdraws through the helper inside one transaction, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker deposits and withdraws through the helper inside one transaction, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `depositLP(uint256 _lpAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
