### Title
Missing zero-amount check before `queueNewRewards`/`safeTransferFrom` can permanently DoS deposit, withdraw and harvest for a Wombat pool - (File: `wombat/WombatStaking.sol`, `rewards/BaseRewardPool.sol`)

### Summary
`WombatStaking._sendRewards` computes a per-fee amount by integer division and forwards it — even when it rounds down to zero — to `IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken)`. `BaseRewardPool.queueNewRewards`/`_provisionReward` (and the equivalent code in `BaseRewardPoolV2` and `mWOMSVBaseRewarder`) unconditionally calls `IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward)` without checking that `_amountReward != 0`. Bonus/bribe reward tokens registered per pool via `addBonusRewardForAsset` are arbitrary external ERC-20s chosen from whatever the underlying Wombat gauge/bribe pays out, and some real-world tokens revert on a zero-value `transferFrom` (the exact class of token described in the source report, e.g. LEND). When such a token is used as a bonus reward and the harvested amount is small enough that `feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR` rounds to 0, the resulting zero-value transfer reverts, and because `_sendRewards` is invoked unconditionally from `_toMasterWomAndSendReward`, this failure propagates all the way up to `deposit`, `depositLP`, `withdraw`, and `harvest` for that pool.

### Finding Description
`_toMasterWomAndSendReward` (`wombat/WombatStaking.sol:671-696`) is called from every core pool operation — `deposit` (line 266), `depositLP` (line 283), `withdraw` (line 304) and `harvest` (line 334) — and internally calls `_sendRewards` for the WOM reward and for every registered bonus token: [1](#0-0) 

`_sendRewards` only guards against the *overall* amount being zero, not the per-fee amount: [2](#0-1) 

`feeAmount` is computed via integer division and can be `0` while `originalRewardAmount > 0` (e.g. a tiny harvested amount with a fee `value` well below `DENOMINATOR`). The resulting `feeTosend = 0` is passed unconditionally to `IBaseRewardPool(feeInfo.to).queueNewRewards(0, rewardToken)`.

`BaseRewardPool.queueNewRewards` → `_provisionReward` performs the transfer without a zero check: [3](#0-2) 

The same missing-zero-check pattern exists in the sibling contracts `BaseRewardPoolV2._provisionReward` and `mWOMSVBaseRewarder._provisionReward`: [4](#0-3) [5](#0-4) 

Bonus reward tokens are registered by the pool via `addBonusRewardForAsset` and originate from whatever the underlying Wombat gauge/bribe distributes — the contract has no control over, and does not restrict, the ERC-20 implementation of these tokens. If a bonus token reverts on a zero-value transfer (a known real-world ERC-20 quirk, exactly the class of token cited in the source report), then every time a harvested bonus-token amount is small enough for a configured fee to round to zero, `queueNewRewards(0, token)` reverts, and since this call is not wrapped in try/catch, the revert bubbles up through `_sendRewards` → `_toMasterWomAndSendReward` → `deposit`/`depositLP`/`withdraw`/`harvest`.

### Impact Explanation
Because `_toMasterWomAndSendReward` runs on every `deposit`, `depositLP`, `withdraw` and `harvest` for the affected pool, once a fee-token pairing enters this zero-rounding condition, all of these entry points become permanently reverting for that pool until the pool configuration is manually changed by governance (removal of the bonus token or fee). Ordinary users lose the ability to deposit or withdraw their LP/receipt tokens through the pool helper, which is a freezing-of-funds condition that persists indefinitely (well beyond 24 hours) since the rounding-to-zero condition recurs any time the per-transaction bonus reward accrual is small relative to `DENOMINATOR` (10000), which is common for low-activity periods or low-volume pools.

### Likelihood Explanation
No privileged action is required to trigger the bug — it is triggered purely by ordinary users calling `deposit`, `depositLP`, `withdraw`, or `harvest`, combined with the pool having a fee configured (`feeInfos`) and a bonus/bribe reward token that reverts on zero transfers. The admin-configured pieces (fee value, bonus token registration) are normal, expected protocol configuration, not malicious/privileged abuse; the vulnerability is purely in the missing zero-amount guard for token transfer/accounting, matching the reported bug class.

### Recommendation
Guard the per-fee transfer path in `WombatStaking._sendRewards` so that `queueNewRewards`/`safeTransfer` is only invoked when `feeTosend > 0`, and add a zero-amount short-circuit at the top of `_provisionReward` in `BaseRewardPool`, `BaseRewardPoolV2`, `mWOMSVBaseRewarder`, and `vlMGPBaseRewarder.queueNewRewards`, mirroring the existing `if (_amount == 0) return;` guard already used elsewhere in `_sendRewards`.

### Proof of Concept
1. Register a Wombat pool via `registerPool` and add a bonus reward token via `addBonusRewardForAsset` whose ERC-20 implementation reverts on `transferFrom(..., 0)` (e.g., an LEND-style token).
2. Configure a fee via `setFee` with `isAddress = false` (routed through `queueNewRewards`) and a nonzero `value`.
3. Have any user call `deposit`/`withdraw`/`harvest` on the pool at a time when the bonus-token harvest amount `originalRewardAmount` is small enough that `feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR == 0`.
4. `_sendRewards` calls `IBaseRewardPool(feeInfo.to).queueNewRewards(0, bonusToken)`, which reverts inside `_provisionReward`'s `safeTransferFrom(msg.sender, address(this), 0)`.
5. The revert propagates to `deposit`/`withdraw`/`harvest`, and every subsequent call to these functions for that pool reverts under the same condition, freezing user funds in the pool.

### Citations

**File:** wombat/WombatStaking.sol (L684-692)
```text
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }
```

**File:** wombat/WombatStaking.sol (L726-758)
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

                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
```

**File:** rewards/BaseRewardPool.sol (L297-302)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
```

**File:** rewards/BaseRewardPoolV2.sol (L290-295)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-310)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
```
