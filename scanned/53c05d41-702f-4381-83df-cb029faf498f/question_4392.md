# Q4392: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
Consider wombat/WombatStaking.sol, where convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Assuming the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged attacker turn this into a divergence between `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` via `convertWOM(uint256 _amount)`, breaking the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the deposit token for the pool is wBNB and the helper arrived through depositNative, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `IMintableERC20(poolInfo.receiptToken).totalSupply()` versus `IMasterWombat(masterWombat) staked balance for poolInfo.pid` relation are unchanged by the attacker's transaction.
