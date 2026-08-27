Based on my analysis, I've confirmed a valid unprivileged-wallet-reachable fee-on-transfer accounting bug in `BaseRewardPool`/`BaseRewardPoolV2`'s `donateRewards` function.

### Title
Fee-on-transfer reward tokens break `donateRewards` accounting in `BaseRewardPool`, causing insolvent reward math and permanent freezing of yield - (File: rewards/BaseRewardPool.sol / rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPool.donateRewards()` (and its `BaseRewardPoolV2` counterpart) is an unpermissioned, externally callable function that lets **any wallet** top up rewards for an already-registered reward token. Internally it calls `_provisionReward`, which trusts the caller-supplied `_amountReward` parameter to compute `rewardPerTokenStored` instead of measuring the actual tokens received via balance-diffing. If the registered reward token is (or later becomes) a fee-on-transfer / deflationary token, the contract will credit stakers with more rewards than it actually holds, permanently under-collateralizing the pool.

### Finding Description
`donateRewards` has no access control beyond requiring the token to already be `isRewardToken[_rewardToken]`: [1](#0-0) 

It forwards straight into `_provisionReward`, which does a `safeTransferFrom` for `_amountReward` and then uses that same nominal `_amountReward` (not the actual balance delta) to update `rewardPerTokenStored`: [2](#0-1) 

The identical pattern exists in `BaseRewardPoolV2`: [3](#0-2) [4](#0-3) 

`rewardPerTokenStored` directly drives `earned()`/claim payouts: [5](#0-4) 

Because `_provisionReward` never checks "before balance vs after balance" of `_rewardToken`, if the token charges a transfer fee (as flagged in the external report for tokens such as STA, PAXG, or potentially future USDT/USDC fee switches), the pool receives less than `_amountReward` but still credits `_amountReward` worth of claimable rewards to `rewardPerTokenStored`. The same trust-the-parameter pattern also appears in the manager-only `queueNewRewards` path used throughout `WombatStaking._sendRewards`, e.g. `IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken)`: [6](#0-5) 
but `queueNewRewards` is manager-gated, whereas `donateRewards` is reachable by any ordinary wallet once a fee-charging token is a registered reward token for a pool — no privileged action is required at the time of exploitation.

### Impact Explanation
Once `rewardPerTokenStored` is inflated beyond the actual token balance held by the `BaseRewardPool`/`BaseRewardPoolV2` contract, later stakers calling `getReward`/`getRewards` will have their `safeTransfer` calls in `_sendReward`/`getReward` revert or drain the contract's balance for that token, since the contract does not hold enough of the reward token to cover all accrued `userRewards`. This results in **permanent freezing/loss of unclaimed yield** for some stakers — first-claimers can drain the shortfall, leaving remaining legitimate reward claims unfulfillable, which is a protocol-insolvency condition scoped as in-scope impact (theft/permanent freezing of unclaimed yield).

### Likelihood Explanation
This requires a reward token registered for a pool (via the manager-gated `queueNewRewards` at pool setup, an intended/normal admin action, not a malicious one) to be, or become, a fee-on-transfer/deflationary token. Given the external report explicitly calls out that mainstream stablecoins like USDT/USDC could add fee-on-transfer behavior in the future, and that the protocol already integrates with third-party reward/bribe tokens from Wombat pools, this is a realistic configuration risk rather than a contrived one. Exploitation itself (calling `donateRewards`) requires zero privilege — any wallet can trigger the broken accounting once such a token is registered.

### Recommendation
In `_provisionReward` (both `BaseRewardPool.sol` and `BaseRewardPoolV2.sol`), measure the actual amount received by diffing `IERC20(_rewardToken).balanceOf(address(this))` before and after the `safeTransferFrom` call, and use that measured delta — not the caller-supplied `_amountReward` — for all downstream accounting (`historicalRewards`, `queuedRewards`, `rewardPerTokenStored`). Optionally, maintain an explicit whitelist/deny-list restricting fee-on-transfer or rebasing tokens from being registered as reward tokens.

### Proof of Concept
1. Admin registers a reward token `FEE` for a pool via `queueNewRewards` (this itself is not malicious; `FEE` may look like a normal ERC20 at registration time or become fee-charging later, e.g., a stablecoin fee switch).
2. Attacker/any user calls `IERC20(FEE).approve(rewardPool, 1000)` then `rewardPool.donateRewards(1000, FEE)`.
3. `_provisionReward` executes `safeTransferFrom(msg.sender, address(this), 1000)`; assume `FEE` charges a 10% fee, so the pool contract actually only receives 900 `FEE` tokens.
4. `_provisionReward` still uses `_amountReward = 1000` to update `rewardInfo.rewardPerTokenStored`, crediting stakers with rewards based on 1000 tokens. [7](#0-6) 
5. When stakers cumulatively call `getReward`, the sum of `userRewards[FEE][account]` payouts can exceed the pool's actual 900-token balance, causing later claims to revert (funds frozen) while earlier claimants may withdraw a disproportionate share, effectively stealing from the entitlements of later claimants.

### Citations

**File:** rewards/BaseRewardPool.sol (L141-185)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }

    function rewardTokenInfos()
        override
        external
        view
        returns
        (
            address[] memory bonusTokenAddresses,
            string[] memory bonusTokenSymbols
        )
    {
        uint256 rewardTokensLength = rewardTokens.length;
        bonusTokenAddresses = new address[](rewardTokensLength);
        bonusTokenSymbols = new string[](rewardTokensLength);
        for (uint256 i; i < rewardTokensLength; i++) {
            bonusTokenAddresses[i] = rewardTokens[i];
            bonusTokenSymbols[i] = IERC20Metadata(address(bonusTokenAddresses[i])).symbol();
        }
    }

    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

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

**File:** rewards/BaseRewardPool.sol (L297-320)
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
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
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

**File:** rewards/BaseRewardPoolV2.sol (L290-314)
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
    }
```

**File:** wombat/WombatStaking.sol (L767-769)
```text
        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
```
