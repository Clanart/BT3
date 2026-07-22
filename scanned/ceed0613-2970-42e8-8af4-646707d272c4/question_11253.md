Q11253: path-direction mismatch in weighted liquidity adder when an exact-output path recurses through a thin intermediate pool

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted` with permit payloads, allowance races, and stale approvals through `selfPermit*` while an exact-output path recurses through a thin intermediate pool, so that the public path, bitmap, and hop token assumptions stop matching the pool actually called along `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity`, corrupting the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions? This public flow intentionally spans two liquidity calculations separated by a user-visible state window, so races and stale assumptions matter. Use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Attacker controls: permit payloads, allowance races, and stale approvals through `selfPermit*`
- Exploit idea: Reach `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity` in a live public flow and show that use a valid-looking path whose repeated token or repeated pool shape stresses the hop-direction derivation and payer updates. The exact value at risk is the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Invariant to test: Each hop must consume the exact token and direction implied by the user-supplied path and bitmap. The concrete assertion should cover the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Expected Immunefi impact: High direct user loss through settlement against the wrong pool or wrong token leg.
- Fast validation: Move the pool between probe and pay phases and assert the cursor bounds, scaled shares, and max token caps either still hold or cause a safe revert.
