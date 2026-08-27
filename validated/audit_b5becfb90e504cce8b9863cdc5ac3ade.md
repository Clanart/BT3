### Title
Hardcoded zero minimum-received disables slippage protection in `ArbWomUp3._deposit`'s WOM→mWOM conversion - (File: wombat/ArbWomUp3.sol)

### Summary
`ArbWomUp3._deposit` (mode 2) calls `SmartWomConvert.convert()` with the `_minRec` slippage parameter hardcoded to `0`, completely disabling the slippage/minimum-output check that `SmartWomConvert._convertFor` otherwise enforces via `MinRecNotMatch`. This mirrors the reported "wrong slippage check" bug class: a slippage-sensitive on-chain operation is executed with an ineffective protection, letting the pool's returned amount be arbitrarily reduced through pool imbalance/front-running with no revert.

### Finding Description
`ArbWomUp3._deposit`, reachable by any wallet through the WOM-deposit flow, splits the deposited WOM and routes half through `SmartWomConvert`: [1](#0-0) 

The call `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` passes `_minRec = 0` for the amount of mWOM the user must receive. Inside `SmartWomConvert._convertFor`, the buyback leg swaps WOM→mWOM on the Wombat router with the router-level minimum also hardcoded to `0`, and the post-swap sufficiency check is only meaningful when `_minRec` is non-zero: [2](#0-1) 

Because `_minRec` is `0`, the check `convertAmount + amountRec < _minRec` at line 204 can never revert, regardless of how little `amountRec` the swap actually returns. This is the same root cause class as the referenced Curve report: a slippage check exists in the code path (`MinRecNotMatch`) but is rendered ineffective by the caller supplying a value that provides no real protection, allowing an imbalanced/pressured pool state (or a sandwich attacker) to make the swap return far less than fair value with no on-chain revert to stop it.

### Impact Explanation
Users depositing WOM through `ArbWomUp3` in mode 2 have half of their WOM routed through an unprotected swap. An attacker (or simply an imbalanced pool at execution time) can cause the swap to return a minimal amount of mWOM, permanently reducing the mWOM/vlMGP-lock value credited to the depositing user — a direct loss of user funds with no possibility of reversion, satisfying the "concrete direct theft of user funds" bar.

### Likelihood Explanation
Any ordinary wallet calling the WOM deposit entrypoint that reaches `_deposit` with mode `2` triggers this path; no privileged role is required. Exploitation only requires a standard sandwich/front-run around the deposit transaction (or occurs naturally if the WOM/mWOM pool is skewed at the time), so likelihood is high given MEV searchers routinely monitor mempools for such patterns.

### Recommendation
Do not hardcode `_minRec` (or the router's internal minimum) to `0`. Require the caller of the top-level deposit function to supply a computed minimum-acceptable mWOM amount (e.g., derived off-chain or from `estimateTotalConversion`) and thread it through to both the `SmartWomConvert.convert` call and the underlying router swap so the existing `MinRecNotMatch` check can actually protect users.

### Proof of Concept
1. A user calls the ArbWomUp3 deposit function with mode `2` for amount `A` WOM.
2. `_deposit` computes `toSwap = A - A/2` and calls `smartWomConvert.convert(toSwap, _convertRatio, 0, 0)`.
3. An attacker sandwiches the transaction (or the WOM/mWOM Wombat pool is already skewed), driving the swap's output `amountRec` far below fair value.
4. `_convertFor`'s check `convertAmount + amountRec < _minRec` (with `_minRec == 0`) never reverts, so the user's deposit completes and they receive materially less mWOM locked on their behalf than expected, with the difference captured by the attacker/pool.

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

**File:** wombat/SmartWomConvert.sol (L186-205)
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
