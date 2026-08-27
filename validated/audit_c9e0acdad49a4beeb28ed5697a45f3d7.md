### Title
Unrestricted `balanceOf`-based sweep of non-compoundable tokens lets attacker drain contract-held ERC20 balances - (File: rewards/ManualCompound.sol)

### Summary
In `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, the "send none compoundable reward back to caller" loop sweeps the entire contract balance of any caller-named token whose `compoundableRewards` flag is `false` — which is the default for every token address, including ones never registered via `addReward`. Because the sweep amount is `IERC20(_rewards[i][j]).balanceOf(address(this))` rather than an amount tied to what was actually claimed for `msg.sender` in this call, any unprivileged caller can drain any ERC20 balance the contract happens to hold under an unregistered token address to themselves.

### Finding Description
`compound` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` [1](#0-0) , then iterates the caller-supplied `_rewards[i][j]` array and, for every token where `compoundableRewards[_rewards[i][j]]` is `false`, transfers `IERC20(_rewards[i][j]).balanceOf(address(this))` — the contract's *total* balance of that token — to `msg.sender`: [2](#0-1) 

`compoundableRewards` is only set `true` for tokens explicitly added by the owner via `addReward`, and defaults to `false` for every other address [3](#0-2) .

The root cause is that the sweep uses `balanceOf(address(this))` instead of a value scoped to what was actually claimed on behalf of `msg.sender` in this specific call. `_rewards[i][j]` is fully attacker-controlled and is never validated to be a real reward that was just credited to the caller — the loop only checks the boolean flag, not provenance of the balance. Any legitimate but unswept balance sitting in the contract (e.g., dust left from a previous `compound` call by another user who omitted that token from their `_rewards` array, tokens still awaiting distribution in the second loop for a token that isn't the caller's, or tokens directly transferred/airdropped to the contract) can be claimed in full by naming that token address, regardless of whether the calling attacker has any actual entitlement to it.

Whether `multiclaimOnBehalf` → `_multiClaim` → `_claimBaseRewarder` → rewarder `getRewards`/`getReward` credits anything for a genuinely unregistered token depends on the specific `BaseRewardPool`/`BaseRewardPoolV2`/`vlMGPBaseRewarder` implementation used for that pool; some variants iterate the caller-supplied `_rewardTokens` list without checking `isRewardToken` before calling `_sendReward` [4](#0-3) , while `donateRewards`/`queueNewRewards` on `BaseRewardPoolV2` show `isRewardToken` gating exists elsewhere in the reward-tracking model [5](#0-4) . Regardless of whether the claim step itself credits anything, the vulnerability in `ManualCompound.compound` is independent: it sweeps *whatever balance currently exists* for the named token, not an amount reconciled to what this caller is owed.

### Impact Explanation
Any balance of a non-registered ERC20 token residing in `ManualCompound` — whether from user error, protocol dust, unswept partial compounds, or accidental transfers — can be fully drained by an unprivileged caller who simply references that token's address in `_rewards`. This is a direct theft-of-funds vector: value that belongs to other users (or the protocol) is transferred to an attacker's own address with no ownership check, matching Critical - Direct theft of user funds.

### Likelihood Explanation
No privileged role is required; `compound` is a public external function callable by any EOA or contract, with `_lps`, `_rewards`, `_convertRatio`, `_minRec`, and `_lockMgp` all attacker-controlled. The only precondition is that the `ManualCompound` contract holds a nonzero balance of some token that is not registered as compoundable — a state that can arise naturally (residual dust from other users' compound calls, tokens sent directly to the contract, or tokens the claim step credits) and is repeatable each time such a balance reappears.

### Recommendation
Do not use `balanceOf(address(this))` to determine the amount to forward for non-compoundable rewards. Instead, capture the balance of each named token immediately before calling `multiclaimOnBehalf` and immediately after, and only forward the *delta* attributable to this specific call/caller. Alternatively, have `multiclaimOnBehalf`/the rewarder return the exact amounts claimed per token per call and use those values directly instead of re-reading contract-wide balances.

### Proof of Concept
Hardhat test plan:
1. Deploy `MasterMagpie`, a `BaseRewardPool`/`vlMGPBaseRewarder` variant, and `ManualCompound`, register one legitimate compoundable reward token via `addReward`.
2. Simulate a "leftover balance" state: transfer some amount of an arbitrary unregistered ERC20 (`tokenX`) directly to the `ManualCompound` contract address (representing dust from a prior incomplete compound or direct transfer), without registering `tokenX` via `addReward`.
3. From an attacker EOA with no stake/no claim in any pool, call `compound(_lps, _rewards, 0, 0, false)` where `_rewards` names a pool with `rewardLength > 0` including `tokenX`.
4. Assert: `tokenX.balanceOf(attacker)` increases by the full previously-held balance, and `tokenX.balanceOf(ManualCompound)` goes to 0, even though the attacker had zero legitimate claim on `tokenX`.
5. Assert this violates the invariant that only the caller's actual claimed share (here, zero) should be transferable.

### Citations

**File:** rewards/ManualCompound.sol (L76-86)
```text
    function addReward(address _tokenAddress, address _tokenHelper, address _convertor, address _locker) external onlyOwner {
        rewards.push(Reward({
            tokenAddress : _tokenAddress,
            tokenHelper : _tokenHelper,
            convertor : _convertor,
            locker : _locker
        }));

        compoundableRewards[_tokenAddress] = true;
        emit RewardAdded(_tokenAddress);
    }
```

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
```

**File:** rewards/ManualCompound.sol (L126-138)
```text
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
```

**File:** rewards/vlMGPBaseRewarder.sol (L248-260)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L252-286)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }

    /* ============ Admin Functions ============ */

    function updateManager(address _rewardManager, bool _allowed) external onlyOwner {
        managers[_rewardManager] = _allowed;

        emit ManagerUpdated(_rewardManager, managers[_rewardManager]);
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
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
```
