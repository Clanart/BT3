### Title
Spot-price manipulation of Wombat AMM pool enables value extraction from protocol reward fees during `WombatStaking` harvest via `SmartWomConvert.smartConvert` - (File: `wombat/SmartWomConvert.sol`, `wombat/WombatStaking.sol`)

### Summary
`WombatStaking._sendRewards()`, which is triggered by the permissionless `harvest()` function, automatically routes a portion of protocol WOM reward fees through `SmartWomConvert.smartConvert()`. This function decides how much WOM to swap on the Wombat `womMWomPool` AMM versus mint 1:1, based entirely on the pool's *instantaneous* spot price (`currentRatio()`) and instantaneous asset cash/liability (`maxSwapAmount()`), both of which are trivially manipulable by any unprivileged wallet within a single transaction. The actual swap executed inside `_convertFor()` also uses a hardcoded `0` minimum-output parameter, giving zero slippage protection at the AMM call site.

### Finding Description
`WombatStaking.harvest()` has no access control beyond the pool being active [1](#0-0) , so any wallet can call it. Internally it calls `_toMasterWomAndSendReward` → `_sendRewards`, which — for reward tokens marked `isMWOM`/wom — invokes the external `smartWomConverter.smartConvert(feeAmount, 0)` on the harvested WOM fee amount [2](#0-1) .

`SmartWomConvert.smartConvert()` computes a `convertRatio` by reading `currentRatio()` — the live spot price of mWom→wom quoted directly from the Wombat router/pool — and, if below `buybackThreshold`, `maxSwapAmount()`, which is derived from the *current* `cash`/`liability` of the `womAsset` Wombat pool asset [3](#0-2) [4](#0-3) .

The subsequent `_convertFor()` executes the actual swap on the same `womMWomPool` with a hardcoded `0` minimum-received parameter, meaning no slippage protection is applied at the AMM level regardless of what the caller intended [5](#0-4) .

This is the same root-cause bug class as the referenced report: a financial decision (how much of the protocol's fee should go through an AMM swap vs. a 1:1 mint) is derived from spot/instantaneous pool state that can be pushed to any value within a single block/transaction by an attacker trading against the `womMWomPool`, and the resulting swap itself has no independent slippage protection.

### Impact Explanation
An attacker can, in one atomic transaction:
1. Swap against the public `womMWomPool` to push the mWom/wom spot price to a favorable extreme (front-run), and/or manipulate `womAsset` cash/liability that feeds `maxSwapAmount()`.
2. Call the permissionless `harvest()` on `WombatStaking`, which drives protocol WOM fee revenue through `SmartWomConvert.smartConvert()`, executing an unprotected swap (`0` minOut) at the manipulated price.
3. Swap back (back-run) to restore the pool and capture the value extracted from the protocol's harvested fee.

Because the WOM fees processed here are protocol revenue that is ultimately queued into `IBaseRewardPool` rewarders for stakers (unclaimed yield) [6](#0-5) , this attack directly extracts value that would otherwise accrue to protocol stakers — a theft of unclaimed yield reachable by any ordinary wallet, without any privileged role.

### Likelihood Explanation
`harvest()` is unauthenticated and callable by anyone whenever a pool is active [1](#0-0) , and it is also triggered incidentally on every deposit/withdraw through `_toMasterWomAndSendReward` [7](#0-6) . The manipulation itself only requires performing a swap on the public Wombat AMM pool immediately before triggering harvest — a single-transaction, no-special-permission, low-risk sandwich attack, consistent with the "no risk, one block" characterization in the referenced report.

### Recommendation
- Do not derive `convertRatio`/`maxSwapAmount` decisions from instantaneous AMM spot price or instantaneous asset cash/liability; use a manipulation-resistant price source (e.g., TWAP) or restrict `smartConvert` calls that affect protocol fee routing to be executed with off-chain-computed, slippage-bounded parameters.
- Replace the hardcoded `0` minimum-output in the internal `swapExactTokensForTokens` call inside `_convertFor` with a caller-supplied, enforced minimum.
- Consider adding access control or a keeper-only restriction, plus commit-reveal/multi-block averaging, around the fee-conversion path in `_sendRewards`.

### Proof of Concept
1. Attacker calls Wombat's public swap functions to move `womMWomPool` reserves so that `SmartWomConvert.currentRatio()` [8](#0-7)  returns a value below `buybackThreshold`, and/or shifts `womAsset` cash/liability to inflate `maxSwapAmount()` [9](#0-8) .
2. In the same transaction, attacker calls `WombatStaking.harvest(_lpToken)` (unauthenticated) [1](#0-0) , which triggers `_sendRewards` → `smartWomConverter.smartConvert(feeAmount, 0)` [10](#0-9) .
3. `smartConvert` computes an inflated `convertRatio`/swap amount from the manipulated state and executes the corresponding swap in `_convertFor` with `0` minOut protection [5](#0-4) , executing at the attacker-set price.
4. Attacker reverses the initial swap to restore the pool and extract the arbitrage profit, at the expense of protocol reward fees destined for `IBaseRewardPool` stakers.

### Citations

**File:** wombat/WombatStaking.sol (L331-335)
```text
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L671-696)
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

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }
```

**File:** wombat/WombatStaking.sol (L739-753)
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
```

**File:** wombat/WombatStaking.sol (L755-769)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
```

**File:** wombat/SmartWomConvert.sol (L98-117)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
    }

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
