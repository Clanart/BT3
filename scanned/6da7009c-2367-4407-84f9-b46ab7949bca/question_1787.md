# Q1787: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, so that `feeInfos[i].value` diverges from `totalFee`, the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM) under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, asserting on every row that an approval on a hot path must be idempotent and must not be able to permanently disable conversion.
