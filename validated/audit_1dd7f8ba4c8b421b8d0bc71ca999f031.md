### Title
Sandwich/MEV attack on reward-to-mWOM conversion due to zero-slippage internal swap in `SmartWomConvert` reachable via `ManualCompound.compound` - ([File: wombat/SmartWomConvert.sol], [File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` lets any wallet convert its own claimed WOM rewards into mWOM through `SmartWomConvert.convertFor`, forwarding a caller-supplied `_minRec` value straight through. Internally, `SmartWomConvert._convertFor` swaps the "buyback" portion of WOM for mWOM on the Wombat router with a hardcoded `0` minimum-out, and only validates the aggregate result against the caller-supplied `_minRec` after the swap executes. This is the same MEV-sandwich pattern described in the SynthChef `reinvest` report: an internal swap executed with `amountOutMin = 0`.

### Finding Description
`ManualCompound.compound()` claims a user's rewards on their behalf and, for any reward token routed through a `convertor`, immediately calls: [1](#0-0) 

which reaches `SmartWomConvert.convertFor` → `_convertFor`, where the actual swap of the "buyback" share of WOM is executed with a hardcoded zero minimum output: [2](#0-1) 

The only protection is a post-hoc aggregate check: [3](#0-2) 

This check only guarantees `convertAmount + amountRec >= _minRec`; it does not bound the price at which the individual swap executes. Because `_minRec` is supplied directly by the transaction sender (`msg.sender` of `compound`), any caller (or any front-end/bot that defaults this parameter to `0`, mirroring the exact misuse pattern in the referenced report) exposes the swap step to a classic sandwich: an MEV bot can front-run with a buy on the WOM/mWOM pool, let the victim's zero-protected swap execute at a degraded price, then back-run to sell, extracting value that would otherwise have gone to the compounding user as mWOM.

### Impact Explanation
The user who calls `ManualCompound.compound()` (or any external caller of `SmartWomConvert.convert`/`convertFor` with `_minRec = 0` or a too-low value) can have the WOM-to-mWOM conversion leg of their claimed, unclaimed-until-that-point yield sandwiched, permanently losing the difference between fair value and the manipulated execution price. Since the swap itself carries zero slippage protection, the loss is bounded only by pool depth/liquidity of the WOM/mWOM pool, and is realized as a one-time, irreversible loss of a portion of the user's yield at the exact moment of conversion.

### Likelihood Explanation
Any Wombat/WOM pool transaction is publicly visible in the mempool prior to inclusion, and `compound`/`convert`/`convertFor` are unauthenticated, permissionless entry points open to any wallet. An MEV searcher merely needs to watch for calls with a low or zero `_minRec` (the parameter is fully attacker-observable) and can systematically sandwich them, exactly mirroring the described SynthChef exploit mechanics.

### Recommendation
Do not rely solely on a post-hoc aggregate `_minRec` check. Require the internal `swapExactTokensForTokens` call in `SmartWomConvert._convertFor` to receive a properly derived, non-zero minimum-out (e.g., computed from `getAmountOut` with a bounded slippage tolerance) rather than the hardcoded `0`, and/or reject `_minRec` values of `0` in `convert`/`convertFor`/`compound` to force callers to specify meaningful slippage protection at the granularity of the swap itself.

### Proof of Concept
1. Alice calls `ManualCompound.compound(lps, rewards, convertRatio, 0, false)` (or her front-end defaults `_minRec` to `0`), which claims her WOM rewards and forwards them to `SmartWomConvert.convertFor(receivedBalance, convertRatio, 0, alice, 2)`. [1](#0-0) 
2. Inside `_convertFor`, the buyback portion of Alice's WOM is swapped via `IWombatRouter.swapExactTokensForTokens(..., 0, address(this), block.timestamp)` with no real minimum-out. [4](#0-3) 
3. An MEV bot observing Alice's pending transaction front-runs it by buying mWOM (or selling WOM) into the `womMWomPool`, causing Alice's swap to execute at a degraded rate, then back-runs to restore the price and capture the difference.
4. Because `_minRec = 0`, the post-swap check `convertAmount + amountRec < _minRec` never reverts, so Alice's transaction succeeds despite receiving far less mWOM than fair value — the value is permanently transferred to the MEV bot. [5](#0-4)

### Citations

**File:** rewards/ManualCompound.sol (L146-150)
```text
            if (receivedBalance > 0) {
                if (_convertor != address(0)) {
                    IERC20(_tokenAddress).safeApprove(_convertor, receivedBalance);
                    IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
                } else if (_locker != address(0) && _lockMgp) {
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

**File:** wombat/SmartWomConvert.sol (L199-207)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;
```
