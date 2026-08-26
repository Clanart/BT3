# Q5701: WombatPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker reach this through `depositLP(uint256 _lpAmount)` while MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
