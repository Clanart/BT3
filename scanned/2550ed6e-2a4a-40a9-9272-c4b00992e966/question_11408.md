Q11408: WETH-native double counting in weighted liquidity adder when the first hop pays from the external caller while later hops pay from router-held balances

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted` with quote or depth reads taken immediately before the user executes the live trade while the first hop pays from the external caller while later hops pay from router-held balances, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity`, corrupting the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions? This public flow intentionally spans two liquidity calculations separated by a user-visible state window, so races and stale assumptions matter. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Move the pool between probe and pay phases and assert the cursor bounds, scaled shares, and max token caps either still hold or cause a safe revert.
