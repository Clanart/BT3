Q11050: probe-pay race in exact-share liquidity adder when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares` with multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput` while an exact-output path recurses through a thin intermediate pool, so that the liquidity-adder probe phase measures one state but the paid phase executes against another reachable state along `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context`, corrupting payer identity, max token caps, callback caller binding, and ownership of the minted position? The user controls payer, owner, salt, and share vector, so callback settlement and position attribution must stay aligned under every valid public combination. Move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Attacker controls: multi-hop paths with repeated tokens or repeated pools in `exactInput` or `exactOutput`
- Exploit idea: Reach `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context` in a live public flow and show that move the pool publicly between probe and payment so the scaled shares no longer correspond to the probed token requirements. The exact value at risk is payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Invariant to test: Weighted liquidity add must either revalidate the probed assumptions or revert; it must never silently mint under a stale quote. The concrete assertion should cover payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Expected Immunefi impact: Medium/High LP-principal loss or broken liquidity add functionality.
- Fast validation: Assert exact-share adds cannot route callback pulls to the wrong pool or mint value into a different owner/salt key than the caller expected.
