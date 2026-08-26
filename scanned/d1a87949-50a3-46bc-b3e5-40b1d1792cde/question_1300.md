# Q1300: WombatPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
Consider wombat/WombatPoolHelper.sol, where _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Assuming the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `depositLP(uint256 _lpAmount)`, breaking the invariant that an approval on the deposit hot path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
