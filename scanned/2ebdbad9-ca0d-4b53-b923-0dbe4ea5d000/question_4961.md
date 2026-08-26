# Q4961: AnkrBNBPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/AnkrBNBPoolHelper.sol: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `depositNative(uint256 _minimumLiquidity)` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has moved the wom/mWom Wombat pool immediately before calling, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
