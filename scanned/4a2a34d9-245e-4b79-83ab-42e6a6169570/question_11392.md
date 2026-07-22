Q11392: permit-order confusion in weighted liquidity adder when a weighted liquidity add uses cursor bounds that hug the active bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted` with quote or depth reads taken immediately before the user executes the live trade while a weighted liquidity add uses cursor bounds that hug the active bin, so that permit execution order lets the router spend a different allowance than the caller intended for the current swap along `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity`, corrupting the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions? This public flow intentionally spans two liquidity calculations separated by a user-visible state window, so races and stale assumptions matter. Mix permit helpers with multicall and swap steps so allowance state differs from what the final payment path assumes.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity` in a live public flow and show that mix permit helpers with multicall and swap steps so allowance state differs from what the final payment path assumes. The exact value at risk is the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Invariant to test: The router must only spend the allowance the current caller intentionally granted for the current transaction path. The concrete assertion should cover the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Expected Immunefi impact: High direct loss if a caller can be induced to spend more than the swap they authorized.
- Fast validation: Move the pool between probe and pay phases and assert the cursor bounds, scaled shares, and max token caps either still hold or cause a safe revert.
