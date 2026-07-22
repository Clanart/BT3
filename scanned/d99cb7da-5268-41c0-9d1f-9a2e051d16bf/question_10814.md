Q10814: stale callback context in exact-share liquidity adder when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares` with `owner`, `salt`, and weighted-share vectors through the liquidity adder while an exact-output path recurses through a thin intermediate pool, so that transient callback authority survives longer than the exact public swap step that created it along `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context`, corrupting payer identity, max token caps, callback caller binding, and ownership of the minted position? The user controls payer, owner, salt, and share vector, so callback settlement and position attribution must stay aligned under every valid public combination. Trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Attacker controls: `owner`, `salt`, and weighted-share vectors through the liquidity adder
- Exploit idea: Reach `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context` in a live public flow and show that trigger a revert or nested router path and then try to make a later public step inherit the stale pool/token/payer context. The exact value at risk is payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Invariant to test: Router callback state must be unique to one live swap step and must be cleared on every success and failure path. The concrete assertion should cover payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Expected Immunefi impact: Critical direct loss if stale callback authority can charge or redirect another user's funds.
- Fast validation: Assert exact-share adds cannot route callback pulls to the wrong pool or mint value into a different owner/salt key than the caller expected.
