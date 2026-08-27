### Title
`incentiveDeposit`'s mode-2 buyback swap is executed with a hardcoded zero minimum-received, allowing sandwich extraction from the WOM/mWom pool - (File: wombat/ArbWomUp3.sol)

### Summary
`_deposit()` in `ArbWomUp3.sol` mode 2 calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`, hardcoding `_minRec` to `0` [1](#0-0) . Inside `SmartWomConvert._convertFor`, that `0` disables both the internal AMM swap's slippage floor (`swapExactTokensForTokens(..., 0, ...)`) and the post-swap `convertAmount + amountRec < _minRec` sanity check [2](#0-1) , so the WOM→mWom leg of the buyback can be executed at an arbitrarily bad price with no revert protection.

### Finding Description
- `_deposit(account, _convertRatio, amount, mode=2)` splits the deposit: half is minted directly via `mWom.deposit`, and the other half (`toSwap`) is routed through `SmartWomConvert.convert(toSwap, _convertRatio, 0, 0)` [3](#0-2) .
- `_convertRatio` only controls the split between `buybackAmount` (swapped through `womMWomPool`) and `convertAmount` (minted 1:1 via `mWom.deposit`) inside `_convertFor` [4](#0-3) ; it is a straight passthrough from the caller of `incentiveDeposit`, there is no separate "reconciliation" step between a caller-supplied ratio and an internally computed one — the claimed divergence mechanism does not exist in this code path.
- The actual defect is that both the AMM call (`swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)`) and the final sanity check (`convertAmount + amountRec < _minRec`) use `_minRec = 0`, so the swap can return an amount of `mWom` far below fair value (or, from an attacker's perspective looking to extract value, far above fair value if the pool is pushed the other way) without any revert [5](#0-4) .
- Since `womMWomPool` is a shared AMM whose price can be moved by any actor with capital (e.g., via a preceding swap or flash loan in the same block), an attacker can: (1) skew the pool's WOM/mWom price in their favor, (2) call `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` so their own `toSwap` WOM converts into an inflated amount of `mWom` (extracted from the pool's existing liquidity/other LPs), which then gets locked into `mWomSV` via `lockFor` in the attacker's name, and (3) unwind the price skew, keeping the extracted `mWom` value.
- `nonReentrant` and `whenNotPaused` on `incentiveDeposit` do not prevent this because the exploit does not require reentrancy — it only requires price manipulation across (or within) a block, which is a standard MEV/sandwich pattern the missing `_minRec` was supposed to prevent.
- Note: the specific precondition proposed in the question (`mWomSV.startUnlock` executed between two `incentiveDeposit` calls) has no code-level connection to the `convert`/`_convertRatio` logic; `startUnlock` does not feed into `_deposit`, `convert`, or `_convertFor` at all, so that particular causal chain is not supported by the code.

### Impact Explanation
This is a genuine missing-slippage-floor bug (real economic value can be pulled out of the shared `womMWomPool`, hurting the pool's liquidity/other participants), matching a "theft of user/protocol funds via unprotected swap" pattern. However, the magnitude is bounded by how much liquidity/price impact the attacker can achieve in `womMWomPool` and by `maxSwapAmount()`-like pool constraints elsewhere in the system (though `_convertFor`'s direct `convert` path used by `ArbWomUp3` does not itself apply `maxSwapAmount`). It is not the "Critical - Direct theft of user funds via a caller-vs-internal ratio divergence" scenario as literally framed in the question, since no such divergence mechanism exists.

### Likelihood Explanation
Exploitability requires only public calls (`incentiveDeposit`, plus an ordinary swap to move the `womMWomPool` price) and no privileged role, so it is technically reachable by any funded EOA/contract. Feasibility and profitability depend on the pool's actual depth and available capital/flash-loan access, which cannot be verified from the code alone (fork test needed against a live pool to confirm a net-positive extraction).

### Recommendation
In `ArbWomUp3._deposit` mode 2, compute a real minimum-received bound (e.g., via `SmartWomConvert.estimateTotalConversion` or an on-chain quote with a caller-supplied slippage tolerance) and pass it as `_minRec` instead of hardcoding `0`; also remove the hardcoded `0` in `SmartWomConvert._convertFor`'s internal `swapExactTokensForTokens` call and rely on the passed `_minRec` consistently.

### Proof of Concept
Foundry fork test plan:
1. Fork mainnet/Arbitrum at a block with live `womMWomPool` liquidity.
2. Have an attacker EOA swap a large amount of WOM/mWom directly against `womMWomPool` (via the Wombat router) to skew the pool price.
3. In the same block, call `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)` from the attacker, choosing `convertRatio` to maximize the `buybackAmount` routed to the mispriced swap.
4. Assert the `mWom` amount locked into `mWomSV` for the attacker (via `lockFor`) exceeds the fair-value quote from `estimateTotalConversion` computed before the price skew, and that `womMWomPool`'s cash/liability changed adversely for other holders.
5. Unwind the attacker's initial pool-skewing swap and assert net attacker profit (post gas) is positive, confirming value extraction enabled by `_minRec = 0`.

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

**File:** wombat/SmartWomConvert.sol (L175-183)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
```

**File:** wombat/SmartWomConvert.sol (L186-207)
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

        obtainedmWomAmount = convertAmount + amountRec;
```
