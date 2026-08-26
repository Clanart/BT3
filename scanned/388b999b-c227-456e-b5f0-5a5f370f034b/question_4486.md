# Q4486: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelper.sol - deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded.
