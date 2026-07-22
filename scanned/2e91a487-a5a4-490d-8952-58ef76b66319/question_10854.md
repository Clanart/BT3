Q10854: path-direction mismatch in exact-share liquidity adder when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares` with `owner`, `salt`, and weighted-share vectors through the liquidity adder while an exact-output path recurses through a thin intermediate pool, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context`, corrupting payer identity, max token caps, callback caller binding, and ownership of the minted position? The user controls payer, owner, salt, and share vector, so callback settlement and position attribution must stay aligned under every valid public combination. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Attacker controls: `owner`, `salt`, and weighted-share vectors through the liquidity adder
- Exploit idea: Reach `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Assert exact-share adds cannot route callback pulls to the wrong pool or mint value into a different owner/salt key than the caller expected.
