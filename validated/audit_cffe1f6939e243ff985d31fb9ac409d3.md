### Title
Blocklisted or paused reward token permanently prevents principal withdrawal in `MasterMagpie` - (File: `rewards/MasterMagpie.sol`)

### Summary
`MasterMagpie._withdraw()` inseparably couples the return of a user's staked principal with the harvesting/transfer of accrued MGP and base-rewarder tokens to that same user. If any of those reward tokens later blocklists the user's address or is paused, the forced reward transfer inside the withdrawal path reverts the entire transaction, permanently trapping the user's staked/receipt tokens in the contract — the same root-cause pattern as the referenced report, where a reward-token transfer to a fixed address blocks retrieval of the staked asset.

### Finding Description
`_withdraw()` calls `_harvestAndUnstake()` before transferring the principal staking token back to the caller: [1](#0-0) 

`_harvestAndUnstake()` unconditionally invokes `_harvestMGP()` and `_harvestBaseRewarder()` for the withdrawing account, and only after these succeed does `_withdraw()` transfer the staking token back to `msg.sender`: [2](#0-1) 

The base-rewarder harvest path resolves to `BaseRewardPoolV2.getReward(_account, _receiver)`, which loops over every registered reward token and pushes any accrued balance directly to the account via `_sendReward`, with no isolation from failures: [3](#0-2) 

Because these reward pushes happen as a mandatory, non-optional sub-call inside the same transaction as the principal `safeTransfer` in `_withdraw`, any reverting ERC20 transfer (e.g., MGP or a bonus/base reward token implementing a blocklist or a pause switch that blocks the specific user) causes the whole `withdraw`/`withdrawFor` call to revert. There is no code path that lets a user retrieve just their principal while skipping a failing reward transfer — mirroring the C4 finding where `unstake()` in `StakingBase` bundled NFT retrieval with a reward transfer to a fixed (now-blocklisted) address, making the entire call unexecutable.

### Impact Explanation
If a reward token used by a `MasterMagpie` pool (MGP itself, or any bonus reward token registered in a `BaseRewardPoolV2`) is paused, or implements a blocklist and later blocklists a specific staker's address, that staker's principal (staking/receipt token) becomes permanently stuck in `MasterMagpie` — they can never call `withdraw`/`withdrawFor` successfully again, since every attempt reverts on the reward transfer. This is a permanent freezing-of-funds condition triggered from an ordinary user's own withdrawal transaction, with no admin or governance action required to enter or exit the state (aside from the reward token issuer's independent blocklist/pause action, which is outside the protocol's control, matching the acknowledged report scenario).

### Likelihood Explanation
Likelihood depends on the protocol allowlisting a reward token that has blocklist/pause capability (e.g., a centralized stablecoin type token) as an MGP bonus reward or MGP token itself gaining such functionality. Given `BaseRewardPoolV2` supports arbitrary registered `rewardTokens` via `donateRewards`/manager-provisioned rewards, this is a realistic configuration risk analogous to the original finding, which was explicitly acknowledged as valid by the referenced protocol's team.

### Recommendation
Decouple principal withdrawal from reward harvesting: allow a user to withdraw their staked/receipt tokens even if a reward-token transfer fails, e.g., by wrapping each reward `_sendReward` call in a try/catch (or checking `code.length`/using a low-level call) and crediting the user's un-transferable reward balance for later retry, rather than reverting the whole withdrawal. This mirrors the "split unstake into two steps" mitigation recommended in the referenced report.

### Proof of Concept
1. A pool's `BaseRewardPoolV2` has a bonus reward token that implements a blocklist (or is pausable).
2. User stakes into `MasterMagpie` via `depositFor`/`deposit`, accruing rewards over time.
3. The reward token blocklists the user's address (or the token is paused).
4. User calls `withdraw`/`withdrawFor` on `MasterMagpie` to retrieve their staked principal.
5. `_withdraw` → `_harvestAndUnstake` → `_harvestBaseRewarder` → `BaseRewardPoolV2.getReward` attempts to `_sendReward` the accrued (now-blocklisted) token to the user, and the ERC20 transfer reverts.
6. The entire `withdraw` transaction reverts, so the principal staking token can never be retrieved by that user while the block/pause persists — for a hard blocklist, this is permanent.

### Citations

**File:** rewards/MasterMagpie.sol (L507-534)
```text
    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L218-235)
```text
    function getReward(address _account, address _receiver)
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
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }

        return true;
    }
```
