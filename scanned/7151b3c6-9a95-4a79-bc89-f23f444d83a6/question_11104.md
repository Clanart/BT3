Q11104: owner-salt misattribution in exact-share liquidity adder when the router already holds leftover WETH or ERC20 from an earlier step in the same transaction

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares` with quote or depth reads taken immediately before the user executes the live trade while the router already holds leftover WETH or ERC20 from an earlier step in the same transaction, so that the public liquidity-adder flow mints or burns value into a different owner/salt identity than the payer intended along `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context`, corrupting payer identity, max token caps, callback caller binding, and ownership of the minted position? The user controls payer, owner, salt, and share vector, so callback settlement and position attribution must stay aligned under every valid public combination. Stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Attacker controls: quote or depth reads taken immediately before the user executes the live trade
- Exploit idea: Reach `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context` in a live public flow and show that stress owner/payer separation and multicall ordering until callback payment and position ownership stop matching. The exact value at risk is payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Invariant to test: Every paid liquidity action must mint value only into the exact owner/salt position encoded in the public request. The concrete assertion should cover payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Expected Immunefi impact: High direct loss if user-paid tokens can be minted into another position key.
- Fast validation: Assert exact-share adds cannot route callback pulls to the wrong pool or mint value into a different owner/salt key than the caller expected.
