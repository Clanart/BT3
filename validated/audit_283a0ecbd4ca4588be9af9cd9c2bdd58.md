I was unable to read the source files for this analysis. The `read_file` tool calls for the key periphery contracts returned errors (missing required params), so I could not inspect the contents of:

- `metric-periphery/contracts/MetricOmmSimpleRouter.sol`
- `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`
- `metric-periphery/contracts/base/PeripheryPayments.sol`
- `metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol`
- `metric-periphery/contracts/lens/MetricOmmSwapQuoter.sol`

Without reading the actual Solidity source, I cannot:
1. Confirm whether any `pay`/sweep/withdrawal path uses `balanceOf(address(this))` when it should use a different balance source (e.g., a pool-tracked balance or a wrapped token balance).
2. Verify whether a zero-amount guard is missing in any callback or payment path.
3. Determine whether existing guards already preserve the invariant.

Due to index size limits, the file contents of these contracts may not be available in the search index. To complete this analysis, start a Devin session with direct filesystem access to read and trace the full source of the periphery contracts listed above.