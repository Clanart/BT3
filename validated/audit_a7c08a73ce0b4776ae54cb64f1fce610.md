### Title
Hardcoded Zero Slippage Protection in ArbWomUp3 WOM-to-mWOM Conversion Enables Sandwich Theft - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit()` is an unprivileged, user-callable function that lets any wallet deposit WOM and, when `_mode == 2`, routes part of the deposit through `SmartWomConvert.convert()` to swap WOM for mWOM before locking it. The internal call hardcodes the `_minRec` (minimum-received) parameter to `0`, which is the exact bug class described in the report: a liquidity/conversion operation that lacks a caller-supplied minimum-output parameter, leaving depositors exposed to front-running/sandwich attacks.

### Finding Description
`ArbWomUp3.incentiveDeposit()` is external and reachable by any ordinary wallet [1](#0-0) . For `_mode == 2` it calls the internal `_deposit`, which splits the WOM amount and forwards half to `SmartWomConvert.convert()`:

```solidity
IERC20(wom).safeApprove(smartWomConvert, toSwap);
IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);
``` [2](#0-1) 

`SmartWomConvert.convert()` forwards to `_convertFor(_amount, _convertRatio, _minRec, _for, _mode)`, whose signature is `(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` [3](#0-2) . Because `ArbWomUp3` hardcodes the `_minRec` argument to `0`, the third positional argument that is supposed to protect the user against slippage is permanently disabled for any deposit routed through this path.

Inside `_convertFor`, the WOM-to-mWOM portion (`buybackAmount`) is swapped through the Wombat router with the router-level `amountOutMin` also hardcoded to `0`:
```solidity
amountRec = IWombatRouter(router).swapExactTokensForTokens(
    tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
);
``` [4](#0-3) 

The only remaining safety net is the post-swap check `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();` [5](#0-4) . Since `_minRec` is `0` when called from `ArbWomUp3`, this check can never revert, so the swap has zero effective slippage protection — an MEV bot can sandwich the swap and extract essentially all of its value, exactly mirroring the reported bug class of liquidity/conversion functions lacking minimum-output parameters.

### Impact Explanation
Any unprivileged user calling `incentiveDeposit(..., _mode = 2)` has the swap-portion of their WOM deposit executed with no slippage floor. A searcher can sandwich the `swapExactTokensForTokens` call in `SmartWomConvert._convertFor`, moving the WOM/mWOM pool price against the victim before the swap and back afterward, extracting the difference as MEV profit. This is a direct theft of user funds: the victim receives fewer mWOM tokens than the fair-market rate, and the deficit is captured by the attacker, with no way for the caller to bound the loss since `_minRec` is not exposed as a user-controllable, meaningfully-set parameter in this call path.

### Likelihood Explanation
The path is trivially reachable by any wallet — `incentiveDeposit` is a plain external function with no access control, and choosing `_mode == 2` (and any `_convertRatio` less than 100%, which is also user supplied but does not fix the hardcoded `_minRec`) triggers the vulnerable swap. Sandwich bots actively monitor mempools for exactly this class of unprotected DEX/AMM swaps, making exploitation straightforward and economically incentivized whenever meaningful WOM amounts are deposited.

### Recommendation
Expose a genuine `_minRec` (or `_minSwapOut`) parameter from `ArbWomUp3.incentiveDeposit()`/`_deposit()` down to the `convert()` call instead of hardcoding `0`, and similarly pass a real `amountOutMin` (derived from `_minRec` and the swap ratio) into the `IWombatRouter.swapExactTokensForTokens` call inside `SmartWomConvert._convertFor`, rather than hardcoding `0` at the router level. This restores the ability for depositors to bound acceptable slippage and prevents the post-hoc aggregate check from being rendered meaningless by an unprotected caller.

### Proof of Concept
1. Attacker monitors mempool for calls to `ArbWomUp3.incentiveDeposit(_amount, _convertRatio, _bullMode, 2)`.
2. Victim's transaction triggers `_deposit` → `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` [6](#0-5) .
3. Inside `SmartWomConvert._convertFor`, the WOM→mWOM swap executes via `IWombatRouter.swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` with no minimum [7](#0-6) .
4. Attacker front-runs with a large WOM→mWOM swap in the same Wombat pool, pushing the price unfavorably for the victim, lets the victim's swap execute at the manipulated price, then back-runs to restore the price and pocket the difference.
5. The post-check `convertAmount + amountRec < _minRec` never reverts because `_minRec == 0`, so the victim's under-priced mWOM is locked into `mWomSV` on their behalf, permanently realizing the loss [8](#0-7) .

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

**File:** wombat/ArbWomUp3.sol (L196-199)
```text

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);
```

**File:** wombat/SmartWomConvert.sol (L121-123)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
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

**File:** wombat/SmartWomConvert.sol (L199-217)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;

        if (_mode == 1) {
            IERC20(mWom).safeApprove(masterMagpie, obtainedmWomAmount);
            IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for);
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
        } else {
            IERC20(mWom).safeTransfer(_for, obtainedmWomAmount);
        }
```
