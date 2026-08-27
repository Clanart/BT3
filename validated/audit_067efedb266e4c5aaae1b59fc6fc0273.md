### Title
`compound()` with empty `_rewards[i]` sweeps all bonus reward tokens into `ManualCompound` with no refund path, permanently freezing non-registered rewards - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` forwards user-supplied `_rewards` arrays to `MasterMagpie.multiclaimOnBehalf`, which in turn calls `_multiClaim` → `_claimBaseRewarder`. When a pool's `_rewardTokens` entry is empty, `_claimBaseRewarder` takes the "claim all" path (`rewarder.getReward(_account, _receiver)`), sending *every* bonus reward token registered on that pool's rewarder to `_receiver` (which is `ManualCompound` itself, since it is the `msg.sender`/compounder). Back in `compound()`, the refund-to-caller loop that returns non-compoundable tokens is gated by `if (rewardLength > 0)`, so it never executes for pools where the caller passed an empty reward array — meaning any bonus token not present in `ManualCompound.rewards` is swept in but never forwarded to the caller nor returned, becoming permanently stuck in the contract.

### Finding Description
`ManualCompound.compound()` [1](#0-0)  calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`. Inside `MasterMagpie`, `multiclaimOnBehalf` sets `_receiver = msg.sender` (the caller of `multiclaimOnBehalf`, i.e. `ManualCompound`) [2](#0-1) , and `_multiClaim` calls `_claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i])` for each pool [3](#0-2) .

`_claimBaseRewarder` contains the exploited branch:
```
if (_rewardTokens.length > 0)
    rewarder.getRewards(_account, _receiver, _rewardTokens);
else
    // if not specifiying any reward token, just claim them all
    rewarder.getReward(_account, _receiver);
``` [4](#0-3) 

Passing an empty inner array `_rewards[i] = []` for a pool with multiple bonus reward tokens forces `getReward()`, the "claim all registered tokens for this pool" path, sending all bonus tokens to `ManualCompound`'s balance rather than only the subset tracked in `compoundableRewards`.

Back in `ManualCompound.compound()`, the refund loop for non-compoundable tokens is:
```
uint256 rewardLength = _rewards[i].length;
if (rewardLength > 0) {
    for (uint j; j < rewardLength; j++) {
        if (!compoundableRewards[_rewards[i][j]]) { ... refund ... }
    }
}
``` [5](#0-4) 

Because it iterates over the *caller-supplied* `_rewards[i]` array (not the actual set of tokens received), an empty `_rewards[i]` means the refund loop body never runs for that pool, regardless of how many tokens were actually pulled in via `getReward()`. The final loop only sweeps tokens matching `rewards[i].tokenAddress` for the fixed, owner-curated `rewards` array [6](#0-5) , so any bonus token not present in that admin-curated list is left sitting in `ManualCompound`'s balance with no code path to return it to the original claimant. There is no owner rescue/sweep function present in this contract for arbitrary ERC20 balances.

No existing modifier prevents this: `compound()` has no access restriction, and `multiclaimOnBehalf`'s `_onlyCompounder` modifier only requires the caller (`ManualCompound`) to be registered as compounder in `MasterMagpie` — it does not validate the shape of `_rewardTokens` passed through. Any unprivileged EOA can call `compound()` directly with a crafted `_rewards` array.

### Impact Explanation
This causes permanent freezing of unclaimed yield: bonus reward tokens legitimately earned by the caller (and by extension routed by the pool's rewarder) get pulled out of the rewarder and become stuck inside `ManualCompound`, unreachable by the caller or any other party, satisfying the "theft or permanent freezing of unclaimed yield" impact class. This is caller-triggerable and not dependent on any admin misconfiguration beyond the normal state where a pool's rewarder has more bonus tokens than `ManualCompound.rewards` tracks (a completely ordinary/expected configuration, not a "misconfiguration").

### Likelihood Explanation
Any unprivileged caller who has staked in a pool with an rewarder tracking more than one bonus token (with only a subset registered in `ManualCompound.rewards`) can trigger this on their very next `compound()` call by simply passing `_rewards[i] = []`. No capital beyond normal staking is required, and the caller loses their own unclaimed bonus rewards outright — no special preconditions, front-running, or flash loans required. Because it can also be triggered against oneself (self-inflicted loss) with certainty of reproducibility, and requires only a standard multi-reward-token pool topology, likelihood is high whenever such pool topology exists.

### Recommendation
Refactor the refund logic in `ManualCompound.compound()` to determine the actually-received tokens rather than relying on the caller-supplied `_rewards[i]` array length. Concretely: force `multiclaimOnBehalf` to be called with the full, canonical list of a pool's registered reward tokens (queried on-chain, e.g. via `IBaseRewardPool.rewardTokens()`), disallow empty `_rewards[i]` for pools where the rewarder has bonus tokens, or add an explicit sweep step post-claim that enumerates the rewarder's full registered reward token list and forwards any balance not in `compoundableRewards` back to `msg.sender`, rather than depending on caller input to know what to refund.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a pool with staking token `LP`, and a rewarder (`BaseRewardPool`/`BaseRewardPoolV2`) registered with 3 bonus reward tokens: `TOKEN_A`, `TOKEN_B`, `TOKEN_C`.
2. Deploy `ManualCompound`, register it as compounder in `MasterMagpie`, and call `addReward` only for `TOKEN_A` (so `compoundableRewards[TOKEN_A] = true`, `TOKEN_B`/`TOKEN_C` untracked).
3. Have attacker EOA stake `LP` into the pool, then have the pool's rewarder accrue nonzero balances of `TOKEN_A`, `TOKEN_B`, `TOKEN_C` for the attacker.
4. Attacker calls `ManualCompound.compound(_lps=[LP], _rewards=[[]], _convertRatio=0, _minRec=0, _lockMgp=false)`.
5. Assert: `TOKEN_A.balanceOf(attacker)` increased (or was routed to convertor/locker/helper) as expected; `TOKEN_B.balanceOf(address(ManualCompound))` and `TOKEN_C.balanceOf(address(ManualCompound))` are nonzero and equal to the amounts claimed from the rewarder; `TOKEN_B.balanceOf(attacker)` and `TOKEN_C.balanceOf(attacker)` remain 0; and after 24+ hours with no owner intervention, those balances in `ManualCompound` remain unchanged and unreachable by the attacker (no function on `ManualCompound` allows withdrawing arbitrary non-registered ERC20 balances back to the original claimant).

### Citations

**File:** rewards/ManualCompound.sol (L123-138)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
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

**File:** rewards/ManualCompound.sol (L139-160)
```text
        for (uint256 i; i< rewardTokensLength; i++) {
            address _tokenAddress = rewards[i].tokenAddress;
            address _helperAddress = rewards[i].tokenHelper;
            address _convertor = rewards[i].convertor;
            address _locker = rewards[i].locker;
            uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));

            if (receivedBalance > 0) {
                if (_convertor != address(0)) {
                    IERC20(_tokenAddress).safeApprove(_convertor, receivedBalance);
                    IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
                } else if (_locker != address(0) && _lockMgp) {
                    IERC20(_tokenAddress).safeApprove(_locker, receivedBalance);
                    ILocker(_locker).lockFor(receivedBalance, msg.sender);                        
                } else if (_helperAddress != address(0)) { 
                    IERC20(_tokenAddress).safeApprove(_helperAddress, receivedBalance);
                    ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender);
                } else {
                    IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance);
                }
            }
        }
```

**File:** rewards/MasterMagpie.sol (L419-424)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool. ONLY callable by compounder!!!!!!
    function multiclaimOnBehalf(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused _onlyCompounder
    {
        _multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L536-561)
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
