## Analog Found

### Title
Ineffective slippage protection in `smartConvert`/`_convertFor` leaks swap value to MEV/sandwich attackers - (`wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.smartConvert` and the internal `_convertFor` it calls perform an on-chain WOM→mWOM swap through `IWombatRouter.swapExactTokensForTokens` with a hard-coded `minAmountOut = 0`, and only validate the *aggregate* result against a self-referential floor (`_amountIn`) rather than a real, caller-supplied expected-output value. This is the same bug class as the referenced Illuminate `yield()` finding: a value that is meant to protect against price manipulation is either unused or checked against a threshold that is trivially satisfied, making the slippage check functionally equivalent to `minOut = 0`.

### Finding Description
`smartConvert` is a public, unprivileged entry point (any wallet holding WOM and having approved the contract can call it directly) that decides how much WOM to swap for mWOM based on the *current spot price* read from the pool via `currentRatio()`: [1](#0-0) 

It then forwards to `_convertFor` with `_minRec = _amountIn`: [2](#0-1) 

Inside `_convertFor`, the actual AMM swap is executed with no per-call slippage protection at all (`0` passed as `minAmountOut`): [3](#0-2) 

The only guard applied afterwards is: [4](#0-3) 

Because `convertAmount + buybackAmount == _amountIn`, this check only enforces `amountRec >= buybackAmount`, i.e. a bare 1:1 floor on the swapped portion. It does **not** protect the *expected* execution price that `smartConvert` itself decided to trade at (the reason it chose to swap at all is that `currentRatio() < buybackThreshold`, i.e., mWOM is trading at a premium relative to WOM, so the caller/protocol expects to receive meaningfully *more* mWOM than the WOM amount swapped). A sandwich attacker can front-run the swap, push the pool price down to just above the 1:1 floor, let `swapExactTokensForTokens` execute at `minOut = 0`, and back-run to restore the price — capturing the premium that should have gone to the caller/protocol, while the transaction still passes the coarse `_minRec` check and does not revert.

This mirrors the original report precisely: a "preview"/decision value (`currentRatio()`) is used to decide trade parameters, but the actual swap has no real slippage floor tied to that preview, and the downstream check is trivially satisfiable, so the effective protection is `minOut = 0`.

### Impact Explanation
Every unprivileged call to `smartConvert` (and by extension any WOM routed through it, e.g. from `WombatStaking.convertAllWom`/harvest flows that funnel WOM into this contract) is exposed to MEV extraction of the WOM/mWOM conversion premium. This is a direct, repeatable value leak on funds moving through a documented conversion path used by the protocol's WOM handling, extractable by any searcher on every call, not a one-off gas/no-impact issue.

### Likelihood Explanation
High: `smartConvert` is externally callable by any wallet with no access control, the underlying router swap always has `minAmountOut = 0`, and the vulnerable condition (mWOM trading below `buybackThreshold` relative to WOM, i.e., a premium exists) is exactly the condition under which the function chooses to execute the swap at all — making the attack profitable and reproducible whenever the function is used as intended.

### Recommendation
- Pass a real, quote-derived minimum output to `swapExactTokensForTokens` inside `_convertFor` (e.g., derived from `IWombatRouter.getAmountOut` at call time with an explicit slippage tolerance, or from a caller-supplied `_minRec` parameter for the swap leg specifically, not just the aggregate final amount).
- Do not use the input amount (`_amountIn`) as a proxy for the expected swap output; the `_minRec` check should bound the actual swap execution price, not merely enforce a 1:1 floor across the whole conversion.
- Consider adding a deadline/oracle-based check on `currentRatio()` staleness to further reduce sandwich exposure.

### Proof of Concept
1. Attacker monitors mempool for calls to `SmartWomConvert.smartConvert` (or WOM flowing in via `convertAllWom`/harvest paths).
2. Attacker front-runs with a large WOM→mWOM (or mWOM→WOM) swap on the `womMWomPool` via the router to move the spot price such that the pending `_convertFor` call's internal `swapExactTokensForTokens(..., 0, ...)` executes at a price barely above 1:1 (just satisfying `convertAmount + amountRec >= _minRec`).
3. The victim's transaction succeeds (no revert, since only the coarse aggregate check applies) but receives materially less mWOM than the premium implied by `currentRatio()` at submission time.
4. Attacker back-runs to restore the pool price and pockets the extracted premium, repeatable on every `smartConvert` call while the buyback condition holds. [5](#0-4)

### Citations

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

**File:** wombat/SmartWomConvert.sol (L186-206)
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

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

```
