### Title
Division-by-zero in `DelegateVoteRewardPool._updateVote` permanently freezes user vote staking/withdrawal once `totalWeight` reaches zero - (File: `rewards/DelegateVoteRewardPool.sol`)

### Summary
`DelegateVoteRewardPool` distributes a user's delegated vlMGP votes across a set of `votePools` proportionally to admin-set `votingWeights`, using `totalWeight` as the denominator. `updateWeight` never validates that `totalWeight` stays non-zero, and `totalWeight` defaults to `0`. Once `totalWeight == 0` while `votePools` is non-empty, every call that triggers `_updateVote()` — reachable by any unprivileged wallet through `stakeFor`/`withdrawFor` — reverts with a division-by-zero, exactly mirroring the Olympus `wallSpread == 10000` bug class where a legitimate admin-set boundary value (not a malicious action) causes a later unprivileged-path calculation to divide by zero and permanently brick that function.

### Finding Description
`_updateVote()` computes, for every registered vote pool, `targetVote = (votingWeights[pool] * totalSupply) / totalWeight`: [1](#0-0) 

`totalWeight` is only ever modified by the unbounded, unchecked admin function `updateWeight`, which does not forbid the value from becoming (or staying) `0`: [2](#0-1) 

Because entries are pushed into `votePools` once (`isVotePool[lp] = true; votePools.push(lp);`) and are never removed, it is fully possible for `votePools.length >= 1` while `totalWeight == 0` — either (a) before the admin has ever configured a non-zero weight for any pool (the default state at deployment), or (b) after the admin zeroes out the last remaining weighted pool (`updateWeight(lp, 0)` on the only pool holding weight, e.g., to disable delegated voting to that pool). Neither case involves malicious admin behavior; it is a routine parameter update analogous to setting `wallSpread_ = 10000` in the referenced report.

Once `totalWeight == 0` and `votePools.length > 0`, `_updateVote()` unconditionally divides by `totalWeight` for every pool in the loop and reverts.

`_updateVote()` is invoked from `stakeFor` and `withdrawFor`, which are `onlyOperator`-gated at the `DelegateVoteRewardPool` level but are called by the `WombatBribeManager` (the `operator`) on behalf of **any unprivileged vlMGP holder** who calls the public `vote()` function targeting the delegated pool: [3](#0-2) [4](#0-3) 

So an ordinary user calling `WombatBribeManager.vote()` with the delegated pool as target — a completely normal, unprivileged action — will unconditionally revert as soon as `totalWeight == 0`, and there is no way to remove entries from `votePools` or otherwise reset the accounting to recover.

### Impact Explanation
When `totalWeight` is `0` while `votePools` is populated:
- No user can `stakeFor` (increase their delegated vote) or `withdrawFor` (decrease/exit their delegated vote) through the delegate pool ever again, since `_updateVote()` always reverts.
- Users who already have vlMGP delegated/staked into this pool become permanently unable to withdraw or rebalance their voting position — their locked voting power (backed by real locked MGP via vlMGP) is stuck in this delegate mechanism indefinitely, a freeze well beyond 24 hours with no admin remediation path once `votePools` already contains entries (weights can be changed, but the array of `votePools` and the frozen call path cannot be bypassed).
- This is a protocol-level, permanent denial of service on a core voting/staking function of `WombatBribeManager`'s delegated-voting flow, directly analogous to `Operator::operate`/`Heart::beat` being permanently bricked in the source report.

### Likelihood Explanation
The zero-`totalWeight` state is reachable through ordinary operational admin actions (not requiring malicious intent): the contract's default `totalWeight` is `0`, and any normal weight-rebalancing call (`updateWeight(lp, 0)` on the sole weighted pool) can reintroduce the zero state at any time after `votePools` already has entries. Given `updateWeight` performs no floor check (no `require(totalWeight != 0)` after update, no minimum-weight rule), this is an easily triggered edge case similar in likelihood to the original medium-severity Olympus finding.

### Recommendation
In `updateWeight`, after updating `totalWeight`, revert if the pool becomes non-empty (`votePools.length > 0`) and `totalWeight == 0`, e.g.:
```solidity
totalWeight = totalWeight - votingWeights[lp] + weight;
votingWeights[lp] = weight;
if (votePools.length > 0 && totalWeight == 0) revert InvalidTotalWeight();
```
Additionally, guard `_updateVote()` itself to skip the proportional calculation (treat `targetVote` as `0`) when `totalWeight == 0`, rather than dividing unconditionally.

### Proof of Concept
1. Admin registers a single pool `lpA` as a vote pool via `updateWeight(lpA, 100)`, making `votePools = [lpA]`, `totalWeight = 100`.
2. A user delegates votes into the delegated pool by calling `WombatBribeManager.vote([delegatedPool], [+X])`, which succeeds (`_updateVote()` computes `100*totalSupply/100`).
3. Admin later calls `updateWeight(lpA, 0)` (a normal rebalancing action, e.g., deprecating `lpA`), setting `totalWeight = 0` while `votePools` still contains `lpA`.
4. Any user (the same or a new one) now calls `WombatBribeManager.vote([delegatedPool], [+/-Y])` to adjust or exit their delegated vote.
5. This reaches `DelegateVoteRewardPool.stakeFor`/`withdrawFor` → `_updateVote()` → `votingWeights[lpA] * totalSupply / totalWeight` divides by `0` and reverts, permanently blocking all future staking/withdrawal through the delegate pool with no recovery path.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L57-82)
```text
    function stakeFor(
        address _for,
        uint256 _amount
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;
        _updateVote();

        emit Staked(_for, _amount);
    }

    function withdrawFor(
        address _for,
        uint256 _amount,
        bool _claim
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;
        _updateVote();

        emit Withdrawn(_for, _amount);

        if (_claim) {
            _getReward(_for);
        }
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L132-143)
```text
    function _updateVote() internal {
        uint256 length = votePools.length;
        int256[] memory deltas = new int256[](length);
        for (uint256 index = 0; index < length; ++index) {
            address pool = votePools[index];
            uint256 targetVote = (votingWeights[pool] * totalSupply) /
                totalWeight;
            uint256 currentVote = _getVoteForLp(pool);
            deltas[index] = int256(targetVote) - int256(currentVote);
        }
        IWombatBribeManager(operator).vote(votePools, deltas);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L207-217)
```text
    function updateWeight(address lp, uint256 weight) external onlyOwner {
        if (lp == address(this)) revert InvalidPoolTokenAddress();
        if (!isVotePool[lp]) {
            if (!IWombatBribeManager(operator).isPoolActive(lp))
                revert PoolNotActive();
            isVotePool[lp] = true;
            votePools.push(lp);
        }
        totalWeight = totalWeight - votingWeights[lp] + weight;
        votingWeights[lp] = weight;
    }
```

**File:** wombat/WombatBribeManager.sol (L182-206)
```text
    function vote(address[] calldata _lps, int256[] calldata _deltas) override public {
        if (_lps.length != _deltas.length)
            revert LengthMismatch();

        uint256 length = _lps.length;
        int256 totalUserVote;

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
            int256 delta = _deltas[i];
            totalUserVote += delta;
            if (delta != 0) {
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
                } else {
                    pool.totalVoteInVlmgp -= uint256(-delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] -= uint256(-delta);
                    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false);
                }
            }
        }
```
