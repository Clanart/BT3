# Q5182: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelper.sol - deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has moved the wom/mWom Wombat pool immediately before calling, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
