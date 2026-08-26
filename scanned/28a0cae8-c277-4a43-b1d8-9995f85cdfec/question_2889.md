# Q2889: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
wombat/WombatPoolHelperV2.sol - depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the caller sets _minAmount to zero on the withdrawal leg, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the caller sets _minAmount to zero on the withdrawal leg, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that native value wrapped for a deposit must always end the transaction attributed to a depositor.
