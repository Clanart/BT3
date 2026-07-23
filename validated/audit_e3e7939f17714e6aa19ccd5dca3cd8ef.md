Audit Report

## Title
Missing Deadline Parameter in `addLiquidityExactShares` Allows Stale Mempool Execution at Adverse Bin Prices — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
Both overloads of `addLiquidityExactShares` in `MetricOmmPoolLiquidityAdder` accept no `deadline` argument and perform no timestamp check before depositing tokens into caller-specified bins. A transaction that sits in the mempool while the oracle-driven pool price moves will execute against stale bin indices, depositing the LP's tokens into out-of-range bins that are immediately subject to adverse selection by the next swap. The `maxAmountToken0`/`maxAmountToken1` caps bound token quantity only, not the price or bin position at which those tokens are deployed.

## Finding Description
`MetricOmmSwapRouterBase._checkDeadline` is called at the top of every swap entry point in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), enforcing `block.timestamp <= deadline` before any pool interaction. `MetricOmmPoolLiquidityAdder` inherits no such guard. Both `addLiquidityExactShares` overloads (L56–68 and L71–81) accept only `pool`, `owner`/`salt`, `deltas` (explicit bin indices + share amounts), `maxAmountToken0`, `maxAmountToken1`, and `extensionData`; there is no `deadline` parameter and no call to `_checkDeadline` or any equivalent timestamp check. The only validation performed is `_validateOwner`, `_validateDeltas`, and the callback-time `MaxAmountExceeded` check (L165–166). None of these checks are sensitive to elapsed time or current oracle price.

Exploit path:
1. Alice calls `addLiquidityExactShares` targeting bin index `B` (the current active bin) with `maxAmountToken0 = X`, `maxAmountToken1 = Y`. The oracle price at submission is `P₀`.
2. The transaction is withheld in the mempool (congestion, low gas, or deliberate MEV withholding).
3. The oracle price moves to `P₁ > P₀`; the pool's active bin advances past `B`.
4. The transaction is included. `addLiquidityExactShares` calls `_addLiquidity` → `IMetricOmmPoolActions(pool).addLiquidity(…, deltas, …)` with the original `deltas` referencing bin `B`. The pool deposits Alice's tokens into bin `B`, which is now below the active bin.
5. Alice's entire deposit is in token0 (the cheaper leg at the stale price). The next sell-token0 swap crosses bin `B` and extracts value from Alice at price `P₀`, not `P₁`. Alice suffers immediate, quantifiable impermanent loss proportional to `P₁/P₀ − 1`.

The `addLiquidityWeighted` overloads have a partial guard (`_validateBinAndBinPosition` at L104/L137), which reverts if the pool cursor has moved outside the caller's specified `minimalCurBin`/`maximalCurBin` window. This does not apply to `addLiquidityExactShares`, which has no cursor check whatsoever.

## Impact Explanation
Direct loss of LP principal. When `addLiquidityExactShares` executes after the oracle price has moved, the deposited tokens are placed into bins that are immediately out-of-range and subject to adverse selection at the stale price. The loss is proportional to the price movement during the mempool delay and is realized as soon as the next swap crosses the affected bins. This meets the "direct loss of user principal above Sherlock thresholds" criterion and the "bad-price execution" criterion (stale bin price reached by the next swap through the LP's position).

## Likelihood Explanation
Any `addLiquidityExactShares` transaction submitted with insufficient gas, during network congestion, or deliberately withheld by a searcher is vulnerable. No special access or privilege is required; the trigger is unprivileged and permissionless. Metric OMM pools are oracle-driven, so the active bin and price update continuously; even moderate delays (minutes) can produce meaningful price divergence. The attack requires no on-chain setup beyond observing a pending transaction in the mempool.

## Recommendation
Add a `uint256 deadline` parameter to both `addLiquidityExactShares` overloads and call `_checkDeadline(deadline)` (or an equivalent inline `if (block.timestamp > deadline) revert …`) as the first statement, mirroring the pattern in `MetricOmmSwapRouterBase._checkDeadline`. Update `IMetricOmmPoolLiquidityAdder` accordingly. The same change should be applied to both `addLiquidityWeighted` overloads for defense-in-depth, since their cursor-bounds check does not protect against price movements within the user's specified window.

## Proof of Concept
```solidity
// Foundry fork test outline
function test_staleExactShares_lossOnPriceMove() public {
    // 1. Record oracle price P0; active bin is B.
    LiquidityDelta memory d = _deltaAtBin(B, 1_000_000);

    // 2. Alice signs addLiquidityExactShares targeting bin B.
    bytes memory call = abi.encodeCall(
        helper.addLiquidityExactShares,
        (address(pool), alice, 1, d, 1_000 ether, 1_000 ether, "")
    );

    // 3. Simulate mempool delay: advance oracle price to P1 = 1.10 * P0,
    //    moving the active bin above B.
    _pushOraclePriceUp(10); // 10% increase

    // 4. Execute Alice's stale transaction.
    vm.prank(alice);
    (uint256 a0, uint256 a1) = helper.addLiquidityExactShares(
        address(pool), alice, 1, d, 1_000 ether, 1_000 ether, ""
    );

    // 5. Alice's deposit landed in bin B (now below active bin).
    //    Verify bin B holds Alice's shares and is below current active bin.
    assertGt(stateView.positionBinShares(address(pool), alice, 1, int8(B)), 0);
    (, int8 curBin,,,, ) = PoolStateLibrary._slot0(address(pool));
    assertGt(int256(curBin), int256(B)); // active bin has advanced past B

    // 6. Execute a sell-token0 swap crossing bin B.
    //    Alice's token0 is consumed at stale price P0 < P1.
    //    Alice's effective entry price is ~10% worse than market.
}
```