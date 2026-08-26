# Q4525: WombatPoolHelper.depositNative - no reentrancy guard anywhere on the helper

## Question
wombat/WombatPoolHelper.sol: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. With msg.value and _minimumLiquidity under attacker control and the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged caller sequence `depositNative(uint256 _minimumLiquidity)` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_liquidity burned via burnReceiptToken` versus `the deposit-token balance delta paid out by WombatStaking.withdraw` relation are unchanged by the attacker's transaction.
