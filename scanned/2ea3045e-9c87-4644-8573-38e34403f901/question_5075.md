# Q5075: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under a large honest deposit is pending in the mempool for the same pool, so that `IERC20(wom).balanceOf(address(this))` diverges from `totalConverted in mWOM`, the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `convertWOM(uint256 _amount)` sequence atomically under a large honest deposit is pending in the mempool for the same pool, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted in mWOM` and the PoC's balance delta is non-positive.
