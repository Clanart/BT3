### Title
`SmartWomConvert` swaps hardcode `deadline = block.timestamp`, denying users a real deadline and exposing WOM→mWOM conversions to stale-price sandwich attacks - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.convert`/`convertFor`/`smartConvert` are unprivileged, user-callable entry points that route part of a user's WOM deposit through a live Wombat AMM swap. The internal helper that performs this swap hardcodes the router's `deadline` argument to `block.timestamp`, exactly the bug class described in the analog report for `RiskFund.swapPoolsAssets`/`_swapAsset`. Because the deadline is evaluated at mining time rather than supplied by the caller at signing time, it provides no actual expiry protection: a transaction can sit in the mempool indefinitely and be executed whenever convenient for a validator/searcher, at which point the swap still "passes" the deadline check trivially.

### Finding Description
`convert`, `convertFor`, and `smartConvert` are external, unprivileged functions that all funnel into `_convertFor`: [1](#0-0) 

Inside `_convertFor`, the "buyback" portion of a user's WOM is swapped for mWOM via `IWombatRouter.swapExactTokensForTokens`, with the deadline hardcoded to `block.timestamp`: [2](#0-1) 

Because `block.timestamp` is evaluated at execution/mining time (not signing time), it can never actually expire a pending transaction — the exact issue identified in the analog report for `RiskFund._swapAsset`. Additionally, note that the swap call itself passes a hardcoded `0` as `minAmountOut` to the router (`buybackAmount, 0, ...`), with slippage only enforced afterward against the aggregate `_minRec` check: [3](#0-2) 

This means the on-chain swap leg has zero independent slippage protection; the only backstop is the caller-supplied `_minRec` applied to the sum of `convertAmount + amountRec`. A stale, long-pending transaction (submitted with too-low gas, or deliberately targeted) can be executed at a manipulated pool price. If the user's chosen `_minRec` has any slack (which is common, since it must also account for the non-swapped `convertAmount` portion), the swap can be pushed to a much worse rate without reverting, transferring the difference to the sandwiching party.

### Impact Explanation
An MEV bot or any party who observes a pending `convert`/`convertFor`/`smartConvert` transaction in the mempool can sandwich the internal `swapExactTokensForTokens` call. Since the deadline auto-adjusts to `block.timestamp` at inclusion time, there is no user-controlled expiry to prevent execution at a stale/manipulated price, and since the swap's own `minAmountOut` is hardcoded to `0`, the swap leg is fully exposed to the pool price at execution time, bounded only by the loose combined `_minRec` check. This results in direct theft of the depositing user's WOM/mWOM value.

### Likelihood Explanation
`convert`/`convertFor`/`smartConvert` are unprivileged and directly callable by any wallet, and `smartConvert` in particular is intended to be triggered automatically (e.g. from `WombatStaking`) with `_minRec` set loosely to `_amountIn` for the whole conversion, not tightly to the swap leg alone, making an unfavorable-but-passing execution plausible whenever mempool conditions or price movement occur between signing and inclusion.

### Recommendation
Add a `deadline` parameter to `convert`, `convertFor`, and `smartConvert` (or to `_convertFor`) that is supplied by the caller and forwarded to `IWombatRouter.swapExactTokensForTokens`, and replace the hardcoded `0` minAmountOut on the swap call with a caller-specified minimum for that specific leg rather than relying solely on the aggregate `_minRec` check.

### Proof of Concept
1. Alice calls `SmartWomConvert.convert(amountIn, convertRatio, minRec, mode)` with a `minRec` that accounts mainly for the un-swapped `convertAmount` and only loosely bounds the swap-derived `amountRec`.
2. The transaction is broadcast but sits in the mempool (low gas fee) while the WOM/mWOM pool price shifts, or a searcher detects it.
3. A searcher sandwiches the internal `router.swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` call — front-running to move the pool price unfavorably and back-running to restore it — extracting value from the `buybackAmount` leg.
4. Because `block.timestamp` at inclusion always satisfies the deadline check, and the swap's own `minAmountOut` is `0`, the trade executes at the manipulated price; as long as `convertAmount + amountRec >= _minRec` still holds (likely given `_minRec` covers the whole conversion, not just the swap), the transaction succeeds with Alice receiving less mWOM than fair value. [4](#0-3)

### Citations

**File:** wombat/SmartWomConvert.sol (L121-147)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
    }

    // should mainly used by wombat staking upon sending wom
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

**File:** wombat/SmartWomConvert.sol (L204-205)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```
