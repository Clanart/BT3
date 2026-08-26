# Q1669: WombatPoolHelperV2.withdraw - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelperV2.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Starting from a state where the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `_minimumLiquidity supplied by the caller` inconsistent with `the LP actually minted by the Wombat pool`, violating the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, snapshot `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
