### Title
Deposit/withdraw/harvest in `WombatStaking` can revert when a dust WOM reward rounds a fee amount to zero and is forwarded to `SmartWomConvert.smartConvert` - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking._sendRewards` computes a per-fee amount by integer division and forwards it unconditionally to `SmartWomConvert.smartConvert`, which reverts on a zero input. When the harvested WOM reward is small enough that `feeAmount` rounds down to zero (while the overall reward is still non-zero and thus not caught by the earlier `_amount == 0` short-circuit), any core user action that triggers a harvest — `deposit`, `depositLP`, `withdraw`, or the public `harvest` — reverts and cannot complete.

### Finding Description
`_sendRewards` is reached from `_toMasterWomAndSendReward`, which is invoked by every deposit, LP deposit, withdrawal, and harvest call in `WombatStaking`: [1](#0-0) 

Inside `_sendRewards`, after the top-level zero check (which only guards the *total* harvested amount, not each fee slice), the fee amount for each active fee entry is computed via integer division: [2](#0-1) 

```
uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
...
if (feeInfo.isMWOM && rewardToken == wom) {
    if (smartWomConverter != address(0)) {
        IERC20(wom).safeApprove(smartWomConverter, feeAmount);
        ...
        IConverter(smartWomConverter).smartConvert(feeAmount, 0);
```

`feeAmount` can round to `0` whenever `originalRewardAmount * feeInfo.value < DENOMINATOR` (e.g. a small harvested WOM amount and/or a small fee percentage), even though `originalRewardAmount > 0` and thus the function did not short-circuit earlier. There is no `feeAmount == 0` guard before calling `smartConvert`.

`SmartWomConvert.smartConvert` explicitly reverts on a zero input: [3](#0-2) 

```
function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
    if (_amountIn == 0) revert MustNoBeZero();
```

This is structurally identical to the Napier `M-10` bug class: a downstream amount is legitimately allowed to become zero through normal arithmetic (buffer/fee rounding), but the calling code does not short-circuit before invoking an external function that unconditionally reverts on zero, causing the entire outer transaction (deposit/withdraw/harvest) to fail.

### Impact Explanation
Because `_toMasterWomAndSendReward` (and therefore `_sendRewards`) is invoked on every `deposit`, `depositLP`, `withdraw`, and `harvest` call in `WombatStaking`, any unprivileged user's deposit or withdrawal attempt can revert whenever the interim WOM reward harvested since the last interaction is small enough to make a configured fee slice round to zero (this is common for pools with low WOM emission allocation, or when deposits/withdrawals happen in quick succession, since each interaction re-triggers a WOM harvest from `MasterWombat`). This directly blocks users from withdrawing their staked LP/receipt tokens and from depositing, breaking core protocol functionality (`WombatPoolHelper.deposit`/`withdraw` ultimately call into this code path) and can persist as long as the reward/fee configuration and low-reward window recur, effectively freezing user funds in that pool until an admin intervenes (e.g., disabling the fee or `smartWomConverter`).

### Likelihood Explanation
This does not require any privileged or malicious actor. It occurs naturally whenever: (1) at least one active fee is configured with `isMWOM = true` and `smartWomConverter` set (an intended production configuration per `WombatStaking.setBribe`/fee setup for MWOM buyback), and (2) the WOM reward accrued between two consecutive interactions on a given pool is small relative to `DENOMINATOR`. Given `_toMasterWomAndSendReward` triggers a real harvest on every deposit/withdraw, back-to-back or frequent user interactions on lower-allocation pools routinely produce such small reward slices, making this reachable through ordinary wallet transactions.

### Recommendation
Short-circuit before calling `smartConvert`/`IMWom.deposit` when `feeAmount == 0`, e.g.:
```solidity
if (feeAmount > 0) {
    if (feeInfo.isMWOM && rewardToken == wom) {
        if (smartWomConverter != address(0)) {
            ...
            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
        } else {
            ...
        }
    }
    ...
}
```
Alternatively, have `smartConvert` return early with `0` instead of reverting when `_amountIn == 0`, mirroring the Napier fix pattern of returning zero rather than reverting on a zero input.

### Proof of Concept
1. Configure a fee entry with `isMWOM = true`, small `value` (e.g. 1 = 0.01%), and set `smartWomConverter` (as in `setBribe`/`addFee`).
2. A pool receives a tiny WOM harvest (e.g., 1–9999 wei of WOM) on a `deposit`/`withdraw` call because little time elapsed since the previous interaction, or because `MasterWombat`'s per-second emission for that pool is small.
3. `_sendRewards` computes `feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR = 0` (rounds down), passing the `_amount == 0` top-level guard since `originalRewardAmount > 0`.
4. `IConverter(smartWomConverter).smartConvert(0, 0)` is called, which reverts with `MustNoBeZero()`.
5. The revert propagates up through `_sendRewards` → `_toMasterWomAndSendReward` → the outer `deposit`/`withdraw`/`harvest` call, so the user's transaction fails and their deposit/withdrawal cannot complete.

### Citations

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

**File:** wombat/WombatStaking.sol (L726-753)
```text
        if (_amount == 0) return;
        uint256 originalRewardAmount = _amount;

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
