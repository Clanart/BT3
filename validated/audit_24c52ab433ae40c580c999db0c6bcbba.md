### Title
Hardcoded `_minRec = 0` in `ArbWomUp3._deposit` Exposes Users to Sandwich Attacks During WOM→mWom Swap - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit` (mode 2) calls `SmartWomConvert.convert()` with the slippage-protection parameter `_minRec` hardcoded to `0`, which completely disables the only slippage/sandwich protection available for the underlying WOM→mWom router swap. This mirrors exactly the reported bug class (hardcoded `amountOutMinimum`/`minRec` to 0 exposing users to sandwich attacks and unlimited slippage), and is directly reachable by any ordinary wallet calling a user-facing deposit function.

### Finding Description
`ArbWomUp3.incentiveDeposit` is a public, unprivileged entry point that any user can call to deposit WOM in exchange for locked vlMGP rewards: [1](#0-0) 

When `_mode == 2`, `_deposit` splits the user's WOM: half is deposited directly to `mWom`, and the other half is routed through `SmartWomConvert.convert(toSwap, _convertRatio, 0, 0)` — note the third argument, `_minRec`, is hardcoded to `0`: [2](#0-1) 

Inside `SmartWomConvert._convertFor`, this `_minRec` value is the *only* protection against an unfavorable swap outcome for the buyback portion (`buybackAmount`) that gets routed through `IWombatRouter.swapExactTokensForTokens`, which itself is called with a hardcoded `amountOutMinimum` of `0`: [3](#0-2) 

The check `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();` is meant to bound the total output, but since the caller (`ArbWomUp3`) passes `_minRec = 0`, this check can never fail — regardless of how bad the swap execution price is. Combined with the router call itself using `0` as `amountOutMinimum`, there is no slippage bound anywhere in this call path.

### Impact Explanation
An attacker monitoring the mempool can sandwich the WOM→mWom swap triggered by any user calling `incentiveDeposit` with `_mode == 2`. By front-running with a large swap in the same direction and back-running to reverse it, the attacker can force the victim's swap to execute at a severely worse price, extracting value directly from the user's WOM at the point of conversion. The resulting `mWomBal` (and thus the amount locked into `mWomSV` on the user's behalf) is reduced, representing a direct extraction of user funds — mWom that should have been received by the user is instead captured by the sandwiching attacker via the pool.

### Likelihood Explanation
This path is triggered by an ordinary user transaction (`incentiveDeposit` with `_mode == 2`) with no privileged role required. Sandwich bots actively monitor DEX/router swap transactions with no slippage protection, so exploitation is straightforward and economically motivated whenever meaningful swap volume passes through.

### Recommendation
Do not hardcode `_minRec`/`amountOutMinimum` to `0`. Either:
- Allow the caller of `incentiveDeposit` to supply a user-specified minimum-received parameter that gets propagated through `_deposit` into `IConverter(smartWomConvert).convert(...)`, or
- Compute an on-chain slippage bound (e.g., using `estimateTotalConversion`/`getAmountOut` with an acceptable slippage tolerance) before calling `convert`.

Additionally, `SmartWomConvert._convertFor` should not hardcode `0` as the `amountOutMinimum` passed directly into `IWombatRouter.swapExactTokensForTokens`; it should derive a proportional minimum for the leg-level swap in addition to the aggregate `_minRec` check.

### Proof of Concept
1. A user calls `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)` to deposit WOM and lock mWom via mode 2.
2. Internally, `_deposit` calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` — `_minRec = 0`.
3. `SmartWomConvert._convertFor` executes `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` with `amountOutMinimum = 0`.
4. An attacker front-runs this transaction with a large WOM→mWom swap on the same pool, pushing the price against the victim, then back-runs to restore the price, capturing the difference.
5. The victim's `_convertFor` call succeeds (since `_minRec = 0` never reverts) but returns a materially smaller `obtainedmWomAmount`, which is then locked into `mWomSV` on the victim's behalf — the lost value is unrecoverable.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

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
