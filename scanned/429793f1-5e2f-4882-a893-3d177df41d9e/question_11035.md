Q11035: WETH-native double counting in exact-share liquidity adder when a quoter result is consumed after a small state-moving transaction but before the user notices

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares` with `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges while a quoter result is consumed after a small state-moving transaction but before the user notices, so that public payment helpers treat existing native ETH and WETH balances as if they belong to the same user step along `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context`, corrupting payer identity, max token caps, callback caller binding, and ownership of the minted position? The user controls payer, owner, salt, and share vector, so callback settlement and position attribution must stay aligned under every valid public combination. Use `msg.value` plus router-held native or WETH residue to see whether a later path receives value twice or from the wrong payer.

Target
- File/function: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Entrypoint: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidityExactShares
- Attacker controls: `zeroForOneBitMap`, `amountInMaximum`, and `amountOutMinimum` around exact-output recursion edges
- Exploit idea: Reach `addLiquidityExactShares -> set pay context -> pool.addLiquidity -> metricOmmModifyLiquidityCallback -> clear pay context` in a live public flow and show that use `msg.value` plus router-held native or weth residue to see whether a later path receives value twice or from the wrong payer. The exact value at risk is payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Invariant to test: Native ETH, WETH deposits, and ERC20 pull settlement must remain attributable to one exact public payment obligation. The concrete assertion should cover payer identity, max token caps, callback caller binding, and ownership of the minted position.
- Expected Immunefi impact: High direct loss or stranded value above contest thresholds.
- Fast validation: Assert exact-share adds cannot route callback pulls to the wrong pool or mint value into a different owner/salt key than the caller expected.
