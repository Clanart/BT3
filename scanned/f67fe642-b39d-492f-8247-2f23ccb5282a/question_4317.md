# Q4317: WombatPoolHelper.deposit - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelper.sol: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. With _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool under attacker control and the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, call `deposit(uint256 _amount, uint256 _minimumLiquidity)`, and assert `pid cached at construction` equals `pools[lpToken].pid in WombatStaking` and that no account can withdraw more than it put in.
