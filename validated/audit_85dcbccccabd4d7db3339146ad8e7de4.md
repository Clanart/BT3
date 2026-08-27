Confirmed: `pendingTokens()` in `IMasterWombat` exposes `bonusTokenAddresses`/`pendingBonusRewards` (Wombat's per-pool bonus reward mechanism, analogous to Balancer returning multiple intermediate tokens), and there is no `rescue`/`sweep` function in `WombatStaking.sol` to recover tokens that fall outside the admin-curated `assetToBonusRewards` list.

### Title
Unregistered Wombat bonus reward tokens are permanently stranded in WombatStaking due to hardcoded bonus-token list - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking._toMasterWomAndSendReward()` only forwards the `wom` token and whatever bonus tokens are pre-registered in `assetToBonusRewards[_lpToken]` via the admin-only `addBonusRewardForAsset()`. Wombat's `MasterWombat` can pay out additional bonus reward tokens per pool (as exposed by `pendingTokens()` returning `bonusTokenAddresses`/`pendingBonusRewards`), but any bonus token not manually added to `assetToBonusRewards` is never detected, never forwarded to the pool's `rewarder`, and is permanently trapped in the `WombatStaking` contract balance — directly mirroring the Balancer report's root cause of only tracking a hardcoded/final token while other tokens legitimately received from a multi-token operation go unaccounted for.

### Finding Description
Every ordinary user action that touches a Wombat pool — `deposit()`, `depositLP()`, `withdraw()`, `harvest()` (all callable by any unprivileged wallet through `WombatPoolHelper`/`WombatPoolHelperV2`) — routes through `_toMasterWomAndSendReward()`: [1](#0-0) 

The function takes `beforeBalances` only for the tokens already listed in `assetToBonusRewards[_lpToken]` via `_rewardBeforeBalances()`: [2](#0-1) 

and only computes/sends deltas for `wom` and those pre-registered bonus tokens: [3](#0-2) 

Any bonus reward token actually paid out by the underlying `MasterWombat`/pool during the `deposit`/`withdraw` call (per the `bonusTokenAddresses` mechanism exposed by `pendingTokens()`) that is **not** in `assetToBonusRewards[_lpToken]` is silently accumulated in the `WombatStaking` contract's balance and never transferred to `poolInfo.rewarder`, so it never becomes claimable by any user via `BaseRewardPool`. Because the accounting is always a "before/after balance diff" pattern taken fresh at the start of each call, any already-accumulated unregistered token balance is baked into the next call's "before" snapshot too — so even a later admin call to `addBonusRewardForAsset()` will only pick up rewards harvested *after* that point, never the funds that already accumulated. There is no `rescue`/`sweep` function anywhere in `WombatStaking.sol` to recover this balance.

This is the direct analog of the Balancer H-1 report: an operation triggerable by ordinary users legitimately returns multiple distinct tokens, but the contract's accounting logic is hardcoded to only recognize a pre-configured subset, so the remainder becomes economically real but functionally invisible to the protocol.

### Impact Explanation
Any bonus reward token paid by Wombat for a pool that is not proactively registered by governance is permanently and irrecoverably stuck in `WombatStaking`, unable to be claimed by any user through the normal flows (`BaseRewardPool`/`WombatPoolHelper`), constituting a permanent freezing of unclaimed yield with no on-chain recovery path. This satisfies the "permanent freezing of unclaimed yield" impact bar, and the funds involved are protocol/user reward funds, not attacker-owned funds, so this is not a self-inflicted/no-impact scenario.

### Likelihood Explanation
Likelihood is driven entirely by unprivileged, routine usage: every `deposit`, `withdraw`, or `harvest` call by any regular staker triggers the flawed accounting path automatically, with no attacker action, special timing, or privileged role required. It only requires Wombat to introduce/enable a bonus reward for a pool that Sentiment/Magpie governance has not yet (or never) added via `addBonusRewardForAsset()` — a realistic and foreseeable operational gap since bonus tokens are configured reactively per pool rather than derived dynamically from the protocol.

### Recommendation
Instead of relying on a static, manually-maintained `assetToBonusRewards` array, dynamically query `IMasterWombat.pendingTokens()` (or equivalent post-call bonus reward return values) before and after the stake/unstake call to determine the actual set of bonus tokens and amounts received, and forward all of them to `poolInfo.rewarder`. As a stopgap, add an owner-only sweep/rescue function that can forward any stray token balance held by `WombatStaking` (excluding LP/receipt tokens under active accounting) to the correct pool's rewarder based on a snapshot reconciliation, so already-stuck balances are not permanently lost.

### Proof of Concept
1. Wombat enables a new bonus reward token `X` for pool `_lpToken` (this is controlled by the external Wombat protocol, not Sentiment/Magpie governance, and can happen without Magpie admin action).
2. Magpie/Sentiment admin has not yet called `addBonusRewardForAsset(_lpToken, X)`, so `assetToBonusRewards[_lpToken]` does not include `X`.
3. A regular user calls `WombatPoolHelper.deposit()` (or `withdraw()`/`harvest()`), which calls `WombatStaking._toMasterWomAndSendReward()` → `_stakeToWombatMaster()`/`IMasterWombat.withdraw()`, which internally credits `WombatStaking` with some amount of token `X` as a bonus reward.
4. `_rewardBeforeBalances()` never snapshots token `X` (not in the list) and `_toMasterWomAndSendReward()`'s loop over `bonusTokens` never includes `X`, so the received `X` balance is neither computed nor forwarded to `poolInfo.rewarder` via `_sendRewards()`.
5. Token `X` sits in `WombatStaking`'s balance indefinitely; even if the admin later calls `addBonusRewardForAsset(_lpToken, X)`, the next harvest's `beforeBalances[i]` snapshot already includes the stuck amount, so only newly-harvested `X` after that point is ever forwarded — the original stuck balance is permanently unrecoverable through any user-facing or admin function in the contract. [4](#0-3)

### Citations

**File:** wombat/WombatStaking.sol (L635-644)
```text
    /// @notice to add bonus token claim from wombat
    function addBonusRewardForAsset(address _lpToken, address _bonusToken) external onlyOwner {
        uint256 length = assetToBonusRewards[_lpToken].length;
        for (uint256 i = 0; i < length; i++) {
            if (assetToBonusRewards[_lpToken][i] == _bonusToken)
                revert BonusRewardExisted();
        }

        assetToBonusRewards[_lpToken].push(_bonusToken);
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

**File:** wombat/WombatStaking.sol (L698-705)
```text
    function _rewardBeforeBalances(address _lpToken) internal view returns(uint256[] memory beforeBalances) {
        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;
        beforeBalances = new uint256[](bonusTokensLength);
        for (uint256 i; i < bonusTokensLength; i++) {
            beforeBalances[i] = IERC20(bonusTokens[i]).balanceOf(address(this));
        }
    }
```
