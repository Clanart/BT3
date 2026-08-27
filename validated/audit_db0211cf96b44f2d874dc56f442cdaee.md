### Title
Permanent Freezing of Locked VLMGP/mWomSV Principal via Forced Bundled Reward Claim in `unlock`/`startUnlock` - ([File: VLMGP.sol / wombat/mWomSV.sol])

### Summary
`VLMGP.startUnlock`/`VLMGP.unlock` and `mWomSV.startUnlock`/`mWomSV.unlock` unconditionally call `IMasterMagpie(masterMagpie).multiclaimFor(...)` before performing the actual unlock logic. This bundles a reward-claim external call chain (which iterates over an arbitrary, admin/manager-extensible list of reward tokens and does `IERC20(rewardToken).safeTransfer(...)` for each one) into the same transaction as unlocking a user's principal. Analogous to the `publicMint` DoS finding, a single failing external call in that chain reverts the whole transaction — but here, unlike a mint that can simply be resubmitted, the failure can be **permanent** for a specific user (e.g., their address is blacklisted by one ERC20 reward token such as a blacklist-capable stablecoin, or a reward token becomes non-transferable/reverts on transfer), locking that user's principal in `VLMGP`/`mWomSV` forever with no path to unlock.

### Finding Description
`startUnlock` and `unlock` in `VLMGP.sol` both perform a full multiclaim before recording/finalizing the unlock: [1](#0-0) [2](#0-1) 

The identical pattern exists in `mWomSV.sol`: [3](#0-2) [4](#0-3) 

`multiclaimFor` routes into `MasterMagpie._multiClaim`, which for every staking token calls `_claimBaseRewarder`, which in turn calls into each `BaseRewardPool` associated with the pool: [5](#0-4) 

`BaseRewardPool.getReward` (invoked as part of that claim path) loops over **all** registered `rewardTokens` for the pool and performs an unconditional `safeTransfer` for each token with a nonzero earned balance: [6](#0-5) 

Reward tokens can be added over time via `queueNewRewards`/`donateRewards` by any authorized manager (e.g. the `WombatBribeManager` forwarding arbitrary bribe tokens harvested from Wombat pools), so the set of tokens a user is forced to receive on every claim/unlock grows and is not user-controlled: [7](#0-6) 

If any single one of these reward tokens cannot be transferred to a specific user — for example, a stablecoin blacklists that user's address, a reward token's contract is paused, or a reward token reverts under certain conditions (fee-on-transfer edge cases, hooks, etc.) — the `safeTransfer` reverts, which bubbles all the way up through `_multiClaim` → `multiclaimFor` → `startUnlock`/`unlock`. Because the claim is a mandatory prerequisite baked into the unlock functions themselves (there is no way to skip or isolate it), the affected user can never successfully call `startUnlock` or `unlock` again as long as that reward token remains in `rewardTokens` and remains untransferable to them.

### Impact Explanation
This differs materially from the `publicMint` DoS, which the project acknowledged as low-severity because failed calls can simply be resubmitted. Here, the reverting condition (e.g., an address-specific blacklist on a reward token) is not transient — resubmitting the transaction does not help, since the same reward token transfer will fail every time. The user's entire locked principal in `VLMGP` (locked MGP) or `mWomSV` (locked mWOM) becomes permanently frozen, since `_unlock`/withdrawal of principal is only reachable through these gated functions. This satisfies the "permanent freezing of funds" impact bar.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a reward token being added to the relevant `BaseRewardPool`(s) tied to `VLMGP`/`mWomSV` staking tokens, which happens organically through bribe/reward routing (e.g. `WombatBribeManager` forwarding bribe tokens), and (b) that token becoming non-transferable to a specific user's address (blacklist, pause, or revert-on-transfer behavior). Given Wombat pool bribes can include arbitrary ERC20 tokens over time and stablecoins with blacklist functionality (e.g. USDC/USDT-style tokens) are common in DeFi reward sets, this is a realistic scenario for at least some subset of users over the life of the protocol, not merely a theoretical/mocked-only condition.

### Recommendation
Decouple reward claiming from unlock/withdraw logic: allow `startUnlock`/`unlock` to proceed even if a reward-token transfer fails (e.g., wrap each token's `safeTransfer` in `BaseRewardPool.getReward` with a try/catch or low-level call and skip failures, accruing the failed amount for later claim), or make claiming during unlock optional rather than mandatory.

### Proof of Concept
1. A reward token `T` (e.g. a stablecoin) is added as a reward token to the `BaseRewardPool` backing the `VLMGP` (or `mWomSV`) staking pool via `queueNewRewards`, which can occur through normal bribe-forwarding flows.
2. `T`'s issuer blacklists a particular user's address (a real-world, non-privileged/non-admin event from the protocol's perspective — it's an action by the token issuer, not the protocol's own privileged role).
3. That user calls `VLMGP.startUnlock` or `VLMGP.unlock` (or the `mWomSV` equivalents) to retrieve their locked principal.
4. The call path `multiclaimFor` → `_multiClaim` → `_claimBaseRewarder` → `BaseRewardPool.getReward` attempts `IERC20(T).safeTransfer(user, reward)`, which reverts due to the blacklist.
5. The entire `startUnlock`/`unlock` transaction reverts. Because the claim is mandatory and unconditional in these functions, the user can never successfully unlock their principal again — their locked MGP/mWOM is permanently frozen.

### Citations

**File:** VLMGP.sol (L275-311)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

**File:** VLMGP.sol (L315-330)
```text
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
```

**File:** wombat/mWomSV.sol (L247-277)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMWOM();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

**File:** wombat/mWomSV.sol (L281-300)
```text
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(mWOM).safeTransfer(msg.sender, unlockedAmount);
```

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
        }
```

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPool.sol (L261-284)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```
