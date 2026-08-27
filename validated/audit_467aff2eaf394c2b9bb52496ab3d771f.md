## Title
Blacklisting of a fee recipient or bonus reward token blocks `deposit`/`withdraw`/`harvest` for an entire Wombat pool, permanently freezing all depositors' LP funds - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking._sendRewards()` performs an unconditional `safeTransfer` of harvested reward/fee tokens to a fixed `feeInfo.to` address (or bonus reward tokens) every time a user calls `deposit()`, `depositLP()`, `withdraw()`, or `harvest()` for a pool. If that reward or bonus token implements a blacklist (e.g. USDC-style tokens) and the recipient address becomes blacklisted, every subsequent call into that shared code path reverts, bricking deposits/withdrawals for **all** users of the pool — not just one actor's funds — exactly analogous to the Axis `pfBidder` blacklist DoS where one address blocked settlement for everyone.

### Finding Description
`_toMasterWomAndSendReward()`, which is invoked from every user-facing entry point (`deposit`, `depositLP`, `withdraw`, `harvest`), calls `_sendRewards()` for the WOM reward and for each configured bonus token: [1](#0-0) 

Inside `_sendRewards()`, harvested reward tokens are distributed to fee recipients via a direct, non-defensive `safeTransfer`: [2](#0-1) 

Specifically, for any active fee entry with `isAddress == true`:
```solidity
IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
``` [3](#0-2) 

This transfer happens unconditionally as part of the reward-harvesting side effect that is bundled into `deposit()`, `withdraw()`, and `harvest()`: [4](#0-3) [5](#0-4) [6](#0-5) 

If the reward token (WOM or any bonus token registered via `addBonusRewardForAsset`) is a blacklist-capable token (e.g. a USDC-style stablecoin used as a bonus reward for an alt pool) and the configured `feeInfo.to` recipient gets blacklisted by that token's issuer after the fee was configured, `safeTransfer` reverts. Because `_sendRewards` is called unconditionally on every `deposit`/`withdraw`/`harvest`, this single reverting transfer blocks **every** user's ability to deposit, withdraw, or harvest for that entire pool — with no way to bypass the reward distribution step, mirroring the referenced bug where a single blacklisted `pfBidder` bricked settlement for all bidders and the seller.

### Impact Explanation
Once the condition is triggered, LP/receipt tokens for all depositors in the affected pool become permanently locked: `withdraw()` cannot complete (it always routes through `_toMasterWomAndSendReward` → `_sendRewards` before returning funds), and `deposit()`/`harvest()` are similarly blocked. This is a protocol-wide, permanent freezing of user funds for the pool, not limited to a single malicious or unlucky account — satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
The precondition is external (a stablecoin-like bonus/fee token blacklisting the configured `feeInfo.to`), which is plausible for real-world tokens such as USDC used as bonus rewards for alt pools, and requires no privileged/malicious admin action — the blacklisting decision is made by the token issuer, not by the protocol. Once it occurs, exploitation is automatic on the very next interaction with the pool.

### Recommendation
Wrap the fee/bonus-token transfer in `_sendRewards()` (and the WOM transfer path) in a try/catch or use a pull-based accounting mechanism so a failing transfer to one fee recipient cannot block the shared deposit/withdraw/harvest flow for all users of the pool.

### Proof of Concept
1. Admin registers a pool with a bonus reward token that is a blacklist-capable stablecoin via `addBonusRewardForAsset`, and configures a fee entry with `isAddress = true` pointing `feeInfo.to` at some address `X` via `addFee`/`setFee`.
2. The bonus token issuer blacklists `X` (independent third-party action).
3. Any user calls `deposit`, `depositLP`, `withdraw`, or `harvest` on the pool; `_toMasterWomAndSendReward` → `_sendRewards` attempts `IERC20(bonusToken).safeTransfer(X, feeTosend)` at [7](#0-6) , which reverts.
4. All users' `withdraw()` calls for the pool now revert permanently, locking their LP positions in `WombatStaking`.

### Citations

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** wombat/WombatStaking.sol (L295-321)
```text
    function withdraw(
        address _lpToken,
        uint256 _liquidity,
        uint256 _minAmount,
        address _sender
    ) nonReentrant whenNotPaused _onlyPoolHelper(_lpToken) external {
        Pool storage poolInfo = pools[_lpToken];

        IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity);
        _toMasterWomAndSendReward(_lpToken, _liquidity, false);

        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );

        emit NewWithdraw(_sender, poolInfo.depositToken, _liquidity);
    }
```

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

**File:** wombat/WombatStaking.sol (L729-770)
```text
        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;

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
    }
```
