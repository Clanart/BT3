This confirms the flow: `_sendRewards` at [1](#0-0)  routes a slice of protocol-harvested WOM (a fee taken from the shared reward pool destined for `BaseRewardPool.queueNewRewards`, i.e., unclaimed yield of stakers) through `SmartWomConvert.smartConvert(feeAmount, 0)` whenever any ordinary user's `deposit`/`withdraw`/`harvest` call triggers a WOM harvest from Wombat's MasterWombat. This is reachable from any wallet since `deposit`/`withdraw`/`harvest` on `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper` are unprivileged entry points that ultimately call `_toMasterWomAndSendReward` → `_sendRewards`.

### Title
Flash-loan price manipulation of `SmartWomConvert` drains protocol WOM buyback funds / stakers' unclaimed yield - (File: wombat/SmartWomConvert.sol)

### Summary
`SmartWomConvert.smartConvert()` decides how much of an incoming WOM amount to swap for `mWom` on the `womMWomPool` AMM based purely on the pool's instantaneous spot price (`currentRatio()`), and then executes that swap through `IWombatRouter.swapExactTokensForTokens` with `amountOutMin` hardcoded to `0`. Because the decision and the execution both rely on manipulable, un-TWAP'd on-chain price with zero slippage protection, an attacker can flash-loan-manipulate the `womMWomPool` reserves immediately before triggering (or being naturally invoked by) `WombatStaking`'s automatic reward-fee conversion, causing protocol WOM (a fee skimmed from stakers' harvested rewards) to be swapped for `mWom` at an attacker-favorable price, extracting value from the pool/protocol at the expense of WOM/mWom stakers.

### Finding Description
`currentRatio()` reads price directly from the AMM in the same block: [2](#0-1)  This spot price feeds `smartConvert()`'s decision of how much of the input WOM must be swapped via `_convertFor`: [3](#0-2)  The actual swap inside `_convertFor` uses `amountOutMin = 0`, i.e., no execution-level slippage protection at all: [4](#0-3)  The only downstream check, `convertAmount + amountRec < _minRec`, is meaningless for protecting the protocol because when `smartConvert` is invoked from `WombatStaking`, `_minRec` is set equal to `_amountIn` itself (the full input), and when invoked as `smartConvert(feeAmount, 0)` from `_sendRewards`, the caller has no way to bound the swap's execution price at all: [5](#0-4) 

This `_sendRewards` path is reached by any unprivileged wallet: calling `deposit`, `withdraw`, or `harvest` on any Wombat pool helper (e.g. `WombatPoolHelper.deposit`/`withdraw`/`harvest`) triggers `WombatStaking._toMasterWomAndSendReward`, which harvests WOM from `MasterWombat` and unconditionally routes a configured fee slice of that harvested WOM into `smartConvert`: [6](#0-5) 

An attacker can, within a single transaction/flash loan: (1) swap a large amount into/out of `womMWomPool` to push the mWom/WOM spot price away from fair value, (2) call any unprivileged pool-helper function (e.g. `harvest()`) so that `_sendRewards` invokes `smartConvert`, forcing the protocol's fee WOM to be swapped into `mWom` at the manipulated, unprotected price, and (3) reverse the initial swap to restore the pool and realize the arbitrage profit taken from the protocol's WOM reserves. Because the fee WOM being converted originates from stakers' harvested rewards en route to `BaseRewardPool.queueNewRewards` (i.e., unclaimed yield), the loss falls on WOM/mWom stakers rather than the attacker.

### Impact Explanation
The manipulated swap directly reduces the amount/value of `mWom` credited back into the reward pipeline relative to fair market value, permanently diverting value from stakers' unclaimed yield to the attacker who manipulated the AMM price around the zero-slippage swap. This matches the "theft ... of unclaimed yield" impact bar, since the loss is realized on-chain within the harvest transaction and is not reversible.

### Likelihood Explanation
`womMWomPool` is a two-asset Wombat pool whose depth is protocol-specific and can plausibly be shallow enough for flash-loan manipulation (consistent with the BGLD-style flash-loan price manipulation report used as the bug-class hint). The trigger condition (any wallet calling `deposit`/`withdraw`/`harvest`) is trivially satisfiable by the attacker themselves within the same transaction as the manipulation, requiring no privileged role, oracle, or governance action.

### Recommendation
- Replace the spot-price read in `currentRatio()`/`maxSwapAmount()` with a time-weighted or otherwise manipulation-resistant price source before deciding buyback ratios.
- Never hardcode `amountOutMin = 0` in `_convertFor`'s router swap; compute a real minimum output from a manipulation-resistant reference price (or pass a caller-supplied, protocol-enforced bound rather than trusting `_minRec` which the caller fully controls).
- Consider guarding `smartConvert`'s pool-facing swap with a maximum single-block price-impact check, or moving the buyback logic off the hot harvest path so it cannot be triggered atomically alongside attacker-controlled pool manipulation.

### Proof of Concept
1. Attacker takes a flash loan of WOM or `mWom` and swaps a large amount in `womMWomPool` via the Wombat router to skew the pool price (e.g., dump `mWom` to depress its price, or dump WOM to inflate `mWom`'s price, depending on which direction benefits the attacker's later arbitrage).
2. In the same transaction, attacker calls any unprivileged entry point that triggers a harvest, e.g. `WombatPoolHelper.harvest()` → `WombatStaking.harvest` → `_toMasterWomAndSendReward` → `_sendRewards`, which calls `SmartWomConvert.smartConvert(feeAmount, 0)` using the now-manipulated `currentRatio()`/`maxSwapAmount()` and executes the WOM→`mWom` swap with `amountOutMin = 0` [4](#0-3) .
3. Attacker reverses the initial pool-skewing swap, restoring the pool and capturing the price-impact spread as profit, while the protocol's fee WOM (stakers' skimmed yield) was converted to `mWom` at the manipulated unfavorable rate.

### Citations

**File:** wombat/WombatStaking.sol (L671-685)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);
```

**File:** wombat/WombatStaking.sol (L739-758)
```text
                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
                    }

                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
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
