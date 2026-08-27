### Title
`SmartWomConvert.smartConvert()` trusts the same-transaction Wombat AMM spot price to decide the buyback ratio, which can be self-manipulated - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.smartConvert()` decides how much of a user's WOM deposit is routed through a market swap ("buyback") versus a fixed 1:1 mint, based on `currentRatio()`, a live spot-price read from the `womMWomPool` via `IWombatRouter.getAmountOut()`. Because this price is read and acted upon within the same transaction, and the subsequent buyback swap is executed with **zero slippage protection**, an unprivileged caller can manipulate the pool state immediately before calling `smartConvert()`/`convert()` to force a favorable buyback execution, extracting value from the `womMWomPool` liquidity at the expense of the pool/protocol, directly mirroring the GMX report's root cause: trusting an on-chain, manipulable AMM price without any external/stale-resistant sanity check.

### Finding Description
`currentRatio()` reads a spot quote from the Wombat router/pool for a fixed notional (`1e18`): [1](#0-0) 

`smartConvert()` uses this single, same-block price to decide `convertRatio`, i.e. how much of the deposited WOM is swapped ("buyback") through the pool versus minted 1:1 via `IMWom.deposit()`: [2](#0-1) 

`maxSwapAmount()` (which bounds how much can be routed to buyback) is computed from the live `cash`/`liability` of the `womAsset`, which is also directly moved by ordinary swaps against the same pool: [3](#0-2) 

The actual buyback swap executed in `_convertFor()` passes a hardcoded `0` as the minimum output to the router, i.e. the swap itself has no independent slippage protection and will always execute at whatever price the pool is in at that moment: [4](#0-3) 

Because both the "should we buyback" decision (`currentRatio()`) and the size cap (`maxSwapAmount()`) are derived from the very pool the attacker can move with an ordinary swap in the same transaction, and the buyback swap that follows has no minOut guard, a caller can:
1. Swap a large amount of mWom into WOM in the `womMWomPool` (via the public Wombat router) to simultaneously push `currentRatio()` below `buybackThreshold` and widen the `womAsset` cash/liability deficit that drives `maxSwapAmount()` up.
2. In the same transaction, call `smartConvert()` (or `convert()`/`convertFor()`) with a large WOM amount, which will now route a maximized `buybackAmount` through the temporarily distorted pool price, buying mWom cheaply from the pool's real reserves.
3. Optionally reverse the initial swap to restore the pool, capturing the value extracted from `womMWomPool` liquidity as profit, with no per-swap slippage check (`0` minOut) to prevent it.

This is the direct analog of the GMX report's root cause: a contract making a monetary decision (minOut/allocation) based on a spot AMM price that is reachable and moveable by the same caller inside a single transaction, without any independent reference check (e.g., TWAP or external price) to detect manipulation.

### Impact Explanation
An attacker can repeatedly extract value from the `womMWomPool`/mWom peg-support liquidity by forcing favorable buyback execution on demand, degrading the pool's WOM backing for mWom redemptions and risking insolvency of the peg mechanism that `SmartWomConvert` and downstream lockers (`mWomSV`, `masterMagpie`) rely on. This is a direct theft-of-funds vector reachable by any ordinary wallet with no special privileges, capitalizable with a flashloan of WOM/mWom.

### Likelihood Explanation
The function is `external` and callable by any wallet (`smartConvert`, `convert`, `convertFor`), the manipulating swap is a standard router call against a public pool, and the exploit requires only capital (obtainable via flashloan) and a single transaction — no privileged role, governance action, or external oracle dependency is required.

### Recommendation
Do not derive `convertRatio`/`maxSwapAmount` decisions from a single same-transaction spot quote of the pool being traded against. Use a time-weighted or otherwise manipulation-resistant reference price (or require the swap path to enforce its own independently-supplied `minOut`/slippage bound instead of hardcoded `0`), and/or restrict `smartConvert`'s buyback-triggering condition to be checked against a price that cannot be moved by the same caller within the same block/transaction.

### Proof of Concept
1. Attacker flashloans a large amount of mWom (or WOM) and swaps it through the Wombat router against `womMWomPool` to push the pool's spot price so that `currentRatio()` < `buybackThreshold` and to widen `womAsset.cash() < womAsset.liability()` (see `wombat/SmartWomConvert.sol:98-117`).
2. In the same transaction, attacker calls `smartConvert(largeWomAmount, mode)` (`wombat/SmartWomConvert.sol:133-147`); `convertRatio` is computed from the manipulated `currentRatio()`/`maxSwapAmount()`, maximizing `buybackAmount`.
3. `_convertFor()` executes `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` (`wombat/SmartWomConvert.sol:193-196`) at the manipulated price, so the attacker's WOM buys mWom from the pool's real reserves at a distorted rate.
4. Attacker reverses the initial swap (or lets normal arbitrage do so) and repays the flashloan, keeping the extra mWom extracted from `womMWomPool` liquidity as profit.

### Citations

**File:** wombat/SmartWomConvert.sol (L98-105)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
    }
```

**File:** wombat/SmartWomConvert.sol (L107-117)
```text
    function currentRatio() public view returns (uint256) {
        address[] memory tokenPath = new address[](2);
        tokenPath[0] = mWom;
        tokenPath[1] = wom;
        
        address[] memory poolPath = new address[](1);
        poolPath[0] = womMWomPool;
    
        (uint256 amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, 1e18);
        return amountOut * DENOMINATOR / 1e18;
    }
```

**File:** wombat/SmartWomConvert.sol (L133-147)
```text
    function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        if (_amountIn == 0) revert MustNoBeZero();

        uint256 convertRatio = DENOMINATOR;
        uint256 mWomToWom = currentRatio();

        if (mWomToWom < buybackThreshold) {
            uint256 maxSwap = maxSwapAmount();
            uint256 amountToSwap = _amountIn > maxSwap ? maxSwap : _amountIn;
            uint256 convertAmount = _amountIn - amountToSwap;
            convertRatio = convertAmount * DENOMINATOR / _amountIn;
        }

        return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L186-197)
```text
        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;
            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;
        
            IERC20(wom).safeApprove(router, buybackAmount);
            amountRec = IWombatRouter(router).swapExactTokensForTokens(
                tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
            );
        }
```
