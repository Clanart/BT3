### Title
Zero-value fee transfer to a revert-on-zero-value-transfer reward token can permanently freeze deposits, withdrawals, and yield distribution - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking._sendRewards` distributes protocol fees to configured fee recipients before forwarding the remaining rewards to a pool's `BaseRewardPool`. When a fee recipient is a plain address (`feeInfo.isAddress == true`), the computed `feeTosend` amount is sent via an unconditional `safeTransfer` with no check that the amount is greater than zero, mirroring the exact bug class described in the external report (`Backstop.claim()` calling `safeTransfer` without checking `balance_ > 0`).

### Finding Description
`_sendRewards` computes `feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR` for each active fee entry [1](#0-0) . Because `DENOMINATOR` is `10000` [2](#0-1) , any sufficiently small `originalRewardAmount` (e.g. dust rewards accrued between harvests, or a low-value bonus token) combined with a small `feeInfo.value` will cause integer division to truncate `feeAmount` (and therefore `feeTosend`) to `0`.

When `feeInfo.isAddress` is `true`, the code performs the transfer unconditionally:
```solidity
} else {
    IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
    emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
}
``` [3](#0-2) 

There is no `if (feeTosend > 0)` guard before this branch, unlike the top-level guard `if (_amount == 0) return;` which only protects against the *aggregate* reward amount being zero, not per-fee dust rounding to zero [4](#0-3) . If `rewardToken` is a revert-on-zero-value-transfer ERC20 (as explicitly assumed in scope by the external report), this `safeTransfer` call reverts.

`_sendRewards` is invoked from `_toMasterWomAndSendReward` [5](#0-4) , which is called unconditionally on every `depositLP`, `withdraw`, and `harvest` call for that pool [6](#0-5) [7](#0-6) [8](#0-7) . All three of these functions are reachable by ordinary unprivileged wallets (directly or via `WombatPoolHelper`) [9](#0-8) .

### Impact Explanation
Once a harvest cycle produces a dust reward amount that rounds a fee to zero for a revert-on-zero-value-transfer token, every subsequent call to `depositLP`, `withdraw`, or `harvest` for that pool reverts, because they all route through `_toMasterWomAndSendReward` → `_sendRewards`. This permanently freezes:
- All users' deposited LP/receipt tokens in that pool (withdrawals revert),
- New deposits into the pool,
- Distribution of unclaimed WOM/bonus-token yield to the pool's `BaseRewardPool`.

This satisfies the "permanent freezing of funds" / "permanent freezing of unclaimed yield" impact bar, since there is no way for an unprivileged user to unstick the pool once the condition is hit (only an owner action such as removing the fee or marking the pool fee-free would fix it, which is outside the accounted flow).

### Likelihood Explanation
The trigger only requires a low-value harvest and a fee/reward-token combination that produces `feeAmount == 0` after integer division, plus a fee-recipient reward token exhibiting revert-on-zero-transfer semantics — both realistic conditions for bonus reward tokens with small per-block emission rates or dust harvests. This exactly reproduces the root cause acknowledged in the external report (missing `balance_ > 0` / amount-zero check before `safeTransfer`).

### Recommendation
Guard the fee transfer with a zero-amount check, mirroring the top-level guard already present in `_sendRewards`:
```solidity
if (!feeInfo.isAddress) {
    ...
} else if (feeTosend > 0) {
    IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
    emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
}
```

### Proof of Concept
1. Owner configures a pool with `assetToBonusRewards` including a token `X` that reverts on zero-value `transfer` calls, and adds an active `Fees` entry with `isAddress = true` and a small `value` relative to `DENOMINATOR` [10](#0-9) .
2. A harvest cycle produces a small `bonusBalanceDiff` for token `X`, such that `_sendRewards` is called with a small `_amount` [11](#0-10) .
3. Inside `_sendRewards`, `feeAmount = (_amount * feeInfo.value) / DENOMINATOR` rounds down to `0`, so `feeTosend = 0` [12](#0-11) .
4. The unconditional `IERC20(rewardToken).safeTransfer(feeInfo.to, 0)` call reverts because token `X` reverts on zero-value transfers [13](#0-12) .
5. Any subsequent unprivileged call to `depositLP`, `withdraw`, or `harvest` for this pool now reverts every time this code path is hit, permanently freezing all users' funds and unclaimed rewards in that pool.

### Citations

**File:** wombat/WombatStaking.sol (L51-57)
```text
    struct Fees {
        uint256 value;              // allocation denominated by DENOMINATOR
        address to;
        bool isMWOM;
        bool isAddress;
        bool isActive;
    }
```

**File:** wombat/WombatStaking.sol (L70-70)
```text
    uint256 constant DENOMINATOR = 10000;
```

**File:** wombat/WombatStaking.sol (L272-287)
```text
    function depositLP(
        address _lpAddress,
        uint256 _lpAmount,
        address _for
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];

        // Transfer lp to this contract and stake it to wombat
        IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);

        _toMasterWomAndSendReward(_lpAddress, _lpAmount, true); // triggers harvest from wombat exchange
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);

        emit NewLPDeposit(_for, poolInfo.lpAddress, _lpAmount, poolInfo.receiptToken, _lpAmount);
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

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
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

**File:** wombat/WombatStaking.sol (L720-727)
```text
    function _sendRewards(
        address _lpToken,
        address _rewardToken,
        address _rewarder,
        uint256 _amount
    ) internal {
        if (_amount == 0) return;
        uint256 originalRewardAmount = _amount;
```

**File:** wombat/WombatStaking.sol (L729-737)
```text
        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;
```

**File:** wombat/WombatStaking.sol (L755-762)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
```

**File:** wombat/WombatPoolHelper.sol (L123-144)
```text
    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }

    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```
