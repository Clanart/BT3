# Q5713: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelper.sol: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, is there an unprivileged sequence of `depositNative(uint256 _minimumLiquidity)` that leaves `IERC20(stakingToken).balanceOf(address(this)) delta` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violates the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded.
