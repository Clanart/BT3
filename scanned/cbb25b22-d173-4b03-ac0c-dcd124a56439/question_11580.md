Q11580: extension-data propagation bug in weighted liquidity adder when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted` with `msg.value` plus WETH input/output paths with partial native balance already on the router while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that per-hop or per-liquidity extension payloads are delivered to a different step than the caller intended along `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity`, corrupting the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions? This public flow intentionally spans two liquidity calculations separated by a user-visible state window, so races and stale assumptions matter. Mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityWeighted
- Attacker controls: `msg.value` plus WETH input/output paths with partial native balance already on the router
- Exploit idea: Reach `weighted add -> probe addLiquidity revert -> scale weights to shares -> paying addLiquidity` in a live public flow and show that mix different extension payloads across hops or liquidity calls and see whether the router/adder forwards them to the wrong protection boundary. The exact value at risk is the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Invariant to test: Each public step must deliver the exact extension payload intended for that step and no other. The concrete assertion should cover the probed token needs, the scaled share vector, cursor bounds, and whether the paid second call still matches the probe assumptions.
- Expected Immunefi impact: High if a guard or accounting extension can be bypassed through wrong payload routing.
- Fast validation: Move the pool between probe and pay phases and assert the cursor bounds, scaled shares, and max token caps either still hold or cause a safe revert.
