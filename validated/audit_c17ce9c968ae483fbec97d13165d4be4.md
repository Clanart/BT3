### Title
First-depositor reward-per-token inflation lets a 1-wei staker steal all queued rewards - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`_provisionReward` in `BaseRewardPoolV2.sol` accumulates reward tokens into `queuedRewards` whenever `totalStaked() == 0`, and later divides `(queuedRewards + newAmount)` by whatever `totalStaked()` happens to be at the moment the next reward is provisioned. Because `totalStaked()` is read live from `IERC20(stakingToken).balanceOf(operator)` [1](#0-0) , and staking via `MasterMagpie.deposit`/`depositFor` is a permissionless, unprivileged action [2](#0-1) , an attacker can stake a trivial 1-wei amount right before the queued rewards are realized, causing nearly the entire `queuedRewards` balance to be converted into `rewardPerTokenStored` while only the attacker holds a non-zero balance.

### Finding Description
The vulnerable logic:
```solidity
if (totalStaked() == 0) {
    rewardInfo.queuedRewards += _amountReward;
} else {
    if (rewardInfo.queuedRewards > 0) {
        _amountReward += rewardInfo.queuedRewards;
        rewardInfo.queuedRewards = 0;
    }
    rewardInfo.rewardPerTokenStored =
        rewardInfo.rewardPerTokenStored +
        (_amountReward * 10**stakingTokenDecimals) / totalStaked();
}
``` [3](#0-2) 

Preconditions require the pool to have zero total stakers for a period while a manager still calls `queueNewRewards` (or anyone calls `donateRewards`, which is unauthenticated) [4](#0-3) [5](#0-4) , accruing a large `queuedRewards` balance. Once the pool is empty, an unprivileged attacker calls `MasterMagpie.deposit(stakingToken, 1)` to stake 1 wei — this function has no minimum-deposit check [6](#0-5) . Immediately after, when `_provisionReward` runs (either via the manager's next `queueNewRewards` call, or — critically — the attacker can trigger it themselves by calling the permissionless `donateRewards` with even 1 wei, requiring no front-running at all), `totalStaked()` returns `1`. The entire accumulated `queuedRewards + newAmount` is divided by `1`, producing an enormous jump in `rewardPerTokenStored`.

Because `userRewardPerTokenPaid[rewardToken][attacker]` was `0` before this jump, `_earned` computes:
```solidity
((_userShare * (rewardPerToken - paid)) / 10**decimals) + userRewards
``` [7](#0-6) 
With `_userShare = 1` and the reward-per-token scaled by `10**decimals`, the attacker's `earned` equals almost the full injected reward amount, which they then withdraw via `getReward`/`getRewards` (`onlyMasterMagpie`, but callable by any staker through `MasterMagpie._multiClaim`/`_claimBaseRewarder`) [8](#0-7) [9](#0-8) .

No existing check prevents this: there is no minimum stake amount, no time-weighting of rewards, and `totalStaked()`/`rewardPerTokenStored` are simple spot-balance/cumulative-index values vulnerable to this classic "empty vault / first depositor" donation attack pattern.

### Impact Explanation
This is a direct theft of unclaimed yield: a 1-wei staker can capture reward tokens that were queued/donated for the benefit of the pool's stakers as a whole, at negligible cost (1 wei of stake plus gas, and optionally a trivial `donateRewards` amount to trigger the conversion themselves). This matches the "theft of unclaimed yield" impact class scoped in the question. If other legitimate users deposit shortly after the attacker's 1-wei stake but before they've been credited any of this jump (since their `userRewardPerTokenPaid` gets set to the already-inflated `rewardPerTokenStored` on their first interaction), they receive none of that reward, and the accumulated yield is instead entirely siphoned to the attacker.

### Likelihood Explanation
- Preconditions: the pool must reach `totalStaked() == 0` at least momentarily, which is realistic — any pool can be temporarily fully unstaked, and a new pool's queued rewards can also accumulate before it has any stakers.
- Capital needed: negligible (1 wei of staking token, no need to even front-run the manager since `donateRewards` is a public unauthenticated entry point that the attacker can call themselves right after staking).
- Feasibility: fully permissionless and repeatable — this can be executed on every pool that manages to empty out even briefly, and is not a one-time flaw.

### Recommendation
- Do not allow `rewardPerTokenStored` to be updated using a `totalStaked()` value that was just created within the same block/transaction as the reward provisioning by a single depositor; alternatively, require a minimum bootstrap stake (e.g., lock a small amount of shares to `address(0)` or the protocol on pool creation, similar to Uniswap V2's `MINIMUM_LIQUIDITY` mechanism) so a single unprivileged staker can never hold 100% of `totalStaked()`.
- Keep `queuedRewards` queued until `totalStaked()` exceeds some meaningful threshold, or spread the queued reward distribution over time (streaming) rather than crediting it entirely to whoever happens to hold the pool's supply at the instant of the next `_provisionReward` call.
- Consider disallowing `donateRewards`/`queueNewRewards` from being provisioned while `totalStaked()` is below a minimum floor, or snapshot `totalStaked()` prior to the same-block deposit to prevent instant-stake capture.

### Proof of Concept
Hardhat test plan (using existing `MasterMagpie`, `BaseRewardPoolV2`, and a mock ERC20 reward/staking token from the repo's mocks):
1. Deploy `MasterMagpie`, register a staking-token pool via `add`, and create a `BaseRewardPoolV2` rewarder via `createRewarder`.
2. Have "victim" users A and B each stake `1000e18` staking tokens via `MasterMagpie.deposit`.
3. Have manager call `queueNewRewards(1000e18, rewardToken)` — verify `rewardPerTokenStored` updates and `earned(A)`/`earned(B)` are non-trivial and roughly proportional.
4. Have A and B fully withdraw via `MasterMagpie.withdraw`, bringing `totalStaked() == 0`.
5. Manager calls `queueNewRewards(1000e18, rewardToken)` again — assert `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored` unchanged (branch `totalStaked()==0` taken) [10](#0-9) .
6. Attacker calls `MasterMagpie.deposit(stakingToken, 1)` (1 wei).
7. Attacker calls `donateRewards(1, rewardToken)` (or wait for the manager's next `queueNewRewards`) — assert `rewardPerTokenStored` jumps to approximately `(1000e18+1) * 10**decimals / 1`.
8. Attacker calls `getReward`/`multiclaim` and assert they receive ~`1000e18` of `rewardToken`, i.e., nearly the entire amount originally queued for prior stakers A and B, despite holding only 1 wei out of the pool's historical stake.
9. Assert `historicalRewards` conservation is broken from the perspective of proportional distribution: sum of A's and B's unclaimed share of the second `1000e18` reward is `0`, while the 1-wei attacker's claim is ~`1000e18`.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L273-286)
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
```

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
```text
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
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/MasterMagpie.sol (L337-339)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L482-505)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```

**File:** rewards/MasterMagpie.sol (L620-629)
```text
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
    }
```
