### Title
Fee-on-transfer reward tokens permanently corrupt BaseRewardPool accounting via unprivileged `donateRewards` - ([File: rewards/BaseRewardPool.sol])

### Summary
`donateRewards` and the shared `_provisionReward` helper in `BaseRewardPool.sol`, `BaseRewardPoolV2.sol`, and `mWOMSVBaseRewarder.sol` credit reward accounting using the caller-supplied `_amountReward` parameter instead of the token amount actually received by the contract, exactly the bug class flagged in the referenced report. Any unprivileged wallet can call this function directly.

### Finding Description
`donateRewards` is a public, permissionless entry point that only checks the token is already registered (`isRewardToken[_rewardToken]`), then calls `_provisionReward`: [1](#0-0) 

`_provisionReward` performs `safeTransferFrom(msg.sender, address(this), _amountReward)` and then immediately uses the same `_amountReward` value — not a before/after `balanceOf` diff — to update `historicalRewards` and, critically, `rewardPerTokenStored`: [2](#0-1) 

The identical pattern exists in `BaseRewardPoolV2.sol`: [3](#0-2) 

and in `mWOMSVBaseRewarder.sol`: [4](#0-3) 

If the reward token registered for a pool applies a transfer fee (deflationary/tax token), the contract receives strictly less than `_amountReward`, yet `rewardPerTokenStored` is inflated as if the full amount arrived. This directly parallels the reported root cause in the original finding (`Basket.sol#L256` trusting a nominal transfer amount instead of the actual balance delta) — here the same anti-pattern lives in the protocol's own core reward-accounting contracts, which are explicitly in scope ("BaseRewardPool reward math").

Reward tokens are not restricted to protocol-controlled tokens: `queueNewRewards` (manager-only) can register arbitrary ERC20s as reward tokens for a pool (e.g., bribe tokens harvested from third-party pools via `WombatBribeManager`/`WombatStaking._sendRewardWithFees`, which calls `IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken)` for arbitrary `rewardToken` addresses): [5](#0-4) 

Once such a token is registered, `donateRewards` becomes callable by any wallet, with no admin involvement, for that token.

### Impact Explanation
Each `donateRewards` call on a fee-on-transfer reward token permanently inflates `rewardPerTokenStored` beyond the actual token balance held by the pool. Since `earned()`/`getReward()` compute claimable amounts from `rewardPerTokenStored`, users' accrued entitlements exceed the real tokens available. Eventually `_sendReward`'s `safeTransfer` will revert for the last claimants due to insufficient balance, permanently freezing a portion of legitimately earned yield for stakers, or effectively making the reward pool insolvent for that token (some stakers can never claim their full entitlement). This satisfies the "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
Likelihood depends on a fee-on-transfer/deflationary ERC20 being registered as a reward token in a `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder` instance (via manager-initiated `queueNewRewards`, including bribe-harvest flows that can route arbitrary third-party tokens). Given that `WombatBribeManager`/bribe reward tokens are external, protocol-agnostic ERC20s chosen by third-party bribers rather than the core team, at least one fee-on-transfer token being registered over the protocol's lifetime is plausible, after which any ordinary wallet can trigger the corruption via `donateRewards` at zero cost beyond the token itself.

### Recommendation
In `_provisionReward` (all three implementations), replace use of the caller-supplied `_amountReward` for accounting with the actual balance delta:
```solidity
uint256 before = IERC20(_rewardToken).balanceOf(address(this));
IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward);
uint256 received = IERC20(_rewardToken).balanceOf(address(this)) - before;
// use `received` in all subsequent historicalRewards / rewardPerTokenStored math
```

### Proof of Concept
1. Manager registers a fee-on-transfer token `T` (e.g. via a bribe harvested through `WombatStaking._sendRewardWithFees` → `queueNewRewards`) as a reward token on `BaseRewardPool` for some pool.
2. Any wallet holding `T` calls `donateRewards(1000e18, T)`.
3. `_provisionReward` calls `safeTransferFrom(msg.sender, address(this), 1000e18)`, but due to `T`'s transfer fee the pool actually receives only e.g. `950e18`.
4. `rewardPerTokenStored` is nonetheless increased using the full `1000e18`, i.e. `rewardInfo.rewardPerTokenStored += (1000e18 * 10**decimals) / totalStaked()`.
5. As stakers accumulate `earned()` balances based on the inflated `rewardPerTokenStored`, the sum of all claimable `T` rewards exceeds the pool's real `T` balance.
6. When the pool's `T` balance is exhausted, subsequent `getReward`/`getRewards` calls for `T` revert on `safeTransfer` inside `_sendReward`, permanently locking the remaining stakers out of already-accrued `T` rewards. [6](#0-5)

### Citations

**File:** rewards/BaseRewardPool.sol (L276-284)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L297-318)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/BaseRewardPoolV2.sol (L323-327)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver, uint256 _amount) internal {
        userRewards[_rewardToken][_account] = 0;
        IERC20(_rewardToken).safeTransfer(_receiver, _amount);
        emit RewardPaid(_account, _receiver, _amount, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L296-326)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    

    /* ============ Internal Functions ============ */

    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
```

**File:** wombat/WombatStaking.sol (L755-758)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
```
