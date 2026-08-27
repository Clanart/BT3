### Title
Zero-slippage WOM→mWOM conversion in `ArbWomUp3._deposit` (mode 2) enables MEV sandwich theft of user funds - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit` (an ordinary, unprivileged user entrypoint) routes part of the user's WOM through `SmartWomConvert.convert(...)` with a hardcoded `_minRec = 0`, and `SmartWomConvert._convertFor` itself performs the underlying AMM swap with a hardcoded `0` minimum-amount-out. Both layers of slippage protection are disabled simultaneously, allowing an MEV searcher to sandwich the swap and steal the difference from the depositing user, whose resulting (undersized) mWOM is then irreversibly locked on their behalf.

### Finding Description
`ArbWomUp3.incentiveDeposit` is callable by any wallet and internally calls `_deposit`, which for `_mode == 2` does: [1](#0-0) 

Here, `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` passes `_minRec = 0` for the aggregate slippage check.

Inside `SmartWomConvert._convertFor`, the buyback portion is swapped through the Wombat router with the min-output hardcoded to `0` at the swap-execution level: [2](#0-1) 

The only remaining guard is the post-swap aggregate check: [3](#0-2) 

Because `ArbWomUp3` passes `_minRec = 0` into `convert(...)`, this aggregate check is also neutralized (`convertAmount + amountRec < 0` never reverts for non-negative amounts). This is precisely the bug class in the analog report: a conditional/optional slippage bound (`useDynamicSlippage` in the report; here, caller-supplied `_minRec`) that can be trivially set to a value that removes all protection, and the call path that reaches it is not restricted to a trusted internal caller with correct guardrails — it is directly reachable by any ordinary user through `incentiveDeposit`.

The obtained (possibly heavily slashed) mWOM is then locked into `mWomSV` on behalf of the user: [4](#0-3) 

### Impact Explanation
An MEV searcher observing a pending `incentiveDeposit(_amount, _convertRatio, false, 2)` transaction can front-run it with a large trade against the WOM/mWOM pool used by `SmartWomConvert` (identified by `womMWomPool`), causing the user's swap to execute at a heavily skewed price with `0` minimum output enforced, and back-run to restore the pool and capture the extracted value. The user's WOM is irreversibly converted and locked at the manipulated rate — this is a direct theft of user funds via sandwich attack, not merely griefing, and requires no user error (no minRec parameter is even exposed by `incentiveDeposit` for the user to protect themselves).

### Likelihood Explanation
High. Every call to `incentiveDeposit` with `_mode == 2` triggers this unprotected swap; no special conditions beyond a searcher watching the mempool and swappable liquidity in the WOM/mWOM pool are required. There are no admin actions or privileged conditions involved — this is triggered by an ordinary wallet's own transaction.

### Recommendation
- Expose a real minimum-received parameter from `incentiveDeposit` through to `SmartWomConvert.convert`, allowing the calling user to set genuine slippage protection instead of hardcoding `0`.
- Remove the hardcoded `0` minAmountOut in `SmartWomConvert._convertFor`'s `swapExactTokensForTokens` call; compute an expected output via `getAmountOut` at call time and enforce a bounded maximum slippage tolerance regardless of caller input, similar to the bounded-slippage-limit fix recommended in the referenced report.
- Add an internal invariant/guardrail in `_convertFor` so that `_minRec == 0` cannot be used to fully bypass protection when the function is reachable from unprivileged, non-atomic-protected call paths.

### Proof of Concept
1. User calls `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)`.
2. This triggers `_deposit(..., 2)` → `IConverter(smartWomConvert).convert(toSwap, convertRatio, 0, 0)`.
3. `SmartWomConvert._convertFor` executes `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` with `0` minimum output [5](#0-4) .
4. An attacker front-runs with a large WOM→mWOM (or mWOM→WOM) swap on the same pool to move the price unfavorably for the pending user transaction, then lets the user's zero-protected swap execute at the skewed price, then back-runs to restore the pool state and pocket the extracted value.
5. Because `_minRec` passed all the way from `ArbWomUp3` is `0` [6](#0-5) , the aggregate check `convertAmount + amountRec < _minRec` never reverts, so the transaction succeeds despite the user receiving far less mWOM than a fair-price conversion would produce, and this reduced amount is permanently locked into `mWomSV` for the user [7](#0-6) .

### Citations

**File:** wombat/ArbWomUp3.sol (L189-203)
```text
        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
```

**File:** wombat/SmartWomConvert.sol (L186-196)
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
```

**File:** wombat/SmartWomConvert.sol (L204-207)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;
```
