### Title
`ManualCompound.compound` sweeps the entire contract balance of every registered reward token to the caller regardless of what that caller actually claimed - (File: rewards/ManualCompound.sol)

### Summary
`compound()` calls `multiclaimOnBehalf` to credit the caller (`msg.sender`) with whatever rewards their own stake in `_lps`/`_rewards` produces, but the settlement loop that follows ignores this and instead sweeps `IERC20(_tokenAddress).balanceOf(address(this))` for *every* token in the globally registered `rewards[]` array to `msg.sender`. Any token balance sitting in the contract that is not attributable to the current caller's claim - stale dust from an earlier `convertFor`/`lockFor`/`depositFor` call that didn't consume 100% of the approved amount, a direct/mistaken transfer, or any other leftover balance - is paid out entirely to whichever unprivileged address next calls `compound()`, even with an empty or unrelated `_lps`/`_rewards` claim.

### Finding Description
`compound()` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` [1](#0-0)  which forwards to `MasterMagpie._multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens)` where `_account = msg.sender` (the compound caller) and `_receiver = msg.sender` of the outer call, i.e. the `ManualCompound` contract itself [2](#0-1) . This correctly scopes the *claim* to the caller's own stake.

However, the settlement/distribution loop that follows is completely decoupled from that claim:
```
for (uint256 i; i< rewardTokensLength; i++) {
    ...
    uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));
    if (receivedBalance > 0) {
        ...
        } else {
            IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance);
        }
    }
}
``` [3](#0-2) 

This loop iterates the entire `rewards` array (`rewardTokensLength = rewards.length`) on every invocation and pays out `balanceOf(address(this))` unconditionally to the current caller for every configured reward token, with no attempt to reconcile that balance against what `multiclaimOnBehalf` actually produced for this specific caller. Since `_lps` and `_rewards` are fully attacker-controlled (including empty arrays), an unprivileged caller can invoke `compound(_lps=[], _rewards=[[]], _convertRatio, _minRec, _lockMgp)`, which claims nothing new, yet the second loop still runs and transfers out any pre-existing balance of a reward token that has no `convertor`/`locker`/`helper` configured directly to `msg.sender` via the plain `safeTransfer` branch.

The precondition (dust/stale balance in the contract) is realistic because:
- `convertFor`/`lockFor`/`depositFor` are external calls to converter/locker/helper contracts that may not consume the full approved `receivedBalance` (fees, rounding, partial acceptance), leaving a remainder in `ManualCompound`.
- Reward tokens can be fee-on-transfer or otherwise leave residual dust across multiple `_multiClaim` calls before someone finally calls `compound()`.
- Any accidental/direct transfer to the contract address becomes instantly sweepable.

### Impact Explanation
Because settlement is balance-based rather than scoped to the caller's own claim, any unswept/residual balance of a reward token (dust from partial conversions, fee-on-transfer remainders, or stray transfers) is not preserved for its rightful owner(s) — it is paid in full to whichever unrelated, unprivileged address next calls `compound()`. This is a direct theft-of-funds primitive: an attacker with zero stake and zero legitimate claim can drain accumulated reward-token balances belonging to the protocol/other users by simply calling `compound()` with an empty or irrelevant claim.

### Likelihood Explanation
No privileged role is required — any EOA can call `compound()` with attacker-chosen `_lps`, `_rewards`, `_convertRatio`, `_minRec`, `_lockMgp`. The only precondition is that some reward token registered in `rewards[]` has no `convertor`/`locker` (or `_lockMgp` is false) and no `tokenHelper` configured, and the contract holds a nonzero balance of that token not yet swept (a realistic and recurring condition given imperfect conversion/lock/deposit consumption and repeated legitimate usage of `compound()` by other users). The attack is repeatable every time such dust accumulates and costs nothing beyond gas.

### Recommendation
Track the actual amount received from `multiclaimOnBehalf` for the current call (e.g., via balance snapshots taken immediately before/after the claim call, or by having `_claimBaseRewarder`/`multiclaimOnBehalf` return per-token amounts), and settle/distribute only that delta, rather than sweeping the contract's full `balanceOf` for every registered reward on every invocation. Additionally, only iterate reward tokens that are actually present in `_rewards` (that this caller's claim touched), not the entire globally registered `rewards[]` array.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `MasterMagpie`, `ManualCompound(masterMagpie)`, and a mock ERC20 reward token `R`. Register `R` via `addReward(R, address(0), address(0), address(0))` (no convertor/locker/helper).
2. Grant `ManualCompound` the compounder role on `MasterMagpie`.
3. Simulate residual dust: have a legitimate user's `compound()` call leave `R` balance in `ManualCompound` (e.g., mock a partial-consuming helper/convertor scenario, or simply `R.transfer(address(ManualCompound), 100e18)` to emulate leftover/fee-on-transfer dust) — this balance belongs conceptually to a prior claimant, not to the next caller.
4. From an unrelated attacker EOA with zero stake in any `_lps` pool, call `compound(_lps=[], _rewards=[], _convertRatio=0, _minRec=0, _lockMgp=false)`.
5. Assert: `multiclaimOnBehalf` claims 0 tokens for the attacker (no `_lps` supplied), yet `R.balanceOf(attacker)` increases by the full 100e18 dust balance, and `R.balanceOf(address(ManualCompound))` goes to 0 — demonstrating `balanceOf(address(this))` is not reconciled with what the caller actually claimed via `multiclaimOnBehalf`.

### Citations

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
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
