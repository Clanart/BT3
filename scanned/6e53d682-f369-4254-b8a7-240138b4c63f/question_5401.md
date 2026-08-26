# Q5401: AnkrBNBPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
wombat/AnkrBNBPoolHelper.sol - none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker controlling _lpAmount, under the receipt token is minted to the helper while the credit is directed at a different address, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the receipt token is minted to the helper while the credit is directed at a different address, then assert `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` end identical in both runs.
