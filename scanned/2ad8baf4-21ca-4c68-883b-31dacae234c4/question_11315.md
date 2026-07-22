Q11315: exact-output overpayment in weighted liquidity adder when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a quoter result is consumed after a small state-moving transaction but before the user notices, so that recursive exact-output accounting grants the output but charges more input than the user-approved maximum should allow along `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity`, corrupting the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions? This public flow intentionally spans two liquidity calculations separated by a user-visible state window, so races and stale assumptions matter. Force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity` in a live public flow and show that force a recursion edge where one hop records the output correctly but another hop overstates the required prior-hop input. The exact value at risk is the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Invariant to test: Exact-output recursion must never charge more than the sum implied by the realized hop outputs and the user's max input. The concrete assertion should cover the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Expected Immunefi impact: Critical direct loss from overpaying input on a publicly callable router path.
- Fast validation: Move the pool between probe and pay phases and assert the cursor bounds, scaled shares, and max token caps either still hold or cause a safe revert.
