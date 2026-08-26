# Q0447: AnkrBNBPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an attacker holding only tokens bought on market reach it via `depositNative(uint256 _minimumLiquidity)` under the pool's deposit token is wBNB and the caller arrived through depositNative and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that an approval on the deposit hot path must be idempotent for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `depositNative(uint256 _minimumLiquidity)` sequence atomically under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
