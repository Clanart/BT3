### Title
`compound()` pays out full contract token balance instead of caller's own claimed delta - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` to claim rewards on behalf of the caller, but then settles each configured reward token using `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` at line 144, rather than tracking the delta produced by that specific call. Any reward tokens that remain on the contract from a prior transaction (dust, a partially-failed transfer, an unclaimed leftover, or tokens deliberately left by a prior caller) get swept up and paid entirely to whoever calls `compound()` next.

### Finding Description
In `compound()` (rewards/ManualCompound.sol:123-163), the caller triggers `multiclaimOnBehalf` which claims the caller's own rewards from `MasterMagpie` into this contract's balance [1](#0-0) . After handling non-compoundable rewards (also using raw `balanceOf`, lines 130-136), the function iterates over all globally configured `rewards[i]` and for each one takes the *entire* current token balance of the contract as `receivedBalance`: [2](#0-1) 
It then routes that whole balance to `msg.sender` — either via a convertor (`IConverter(_convertor).convertFor(...)`), a locker, a helper deposit, or a direct `safeTransfer`, all targeting `msg.sender`, in lines 146-159 [3](#0-2) .

Because `receivedBalance` is derived from `balanceOf(address(this))` rather than a before/after delta scoped to this caller's own `multiclaimOnBehalf` result, any token balance already resident on the contract (e.g., from a previous caller whose flow reverted mid-way, dust left from rounding, a direct token transfer sent by anyone, or an interleaved compound where the previous caller's transfer step failed silently) is fully captured by the current caller. There is no accounting structure (no per-user or per-call tracked "amount claimed this tx") to reconcile `balanceOf(address(this))` against "the caller's own share." This violates the intended invariant that a compounder should only receive value proportional to their own claim.

### Impact Explanation
This is a **direct theft of user funds** vector: an unprivileged caller who is aware of (or who engineers) a nonzero pre-existing balance of a `rewards[i].tokenAddress` on the `ManualCompound` contract can call `compound()` with a claim of even 0 or minimal value and receive the entire outstanding balance meant for other users/protocol accounting. This matches Immunefi Critical - Direct theft of user funds.

### Likelihood Explanation
The precondition is that the contract holds a nonzero balance of a configured reward token that does not belong to the current caller. This can arise naturally from dust/rounding in `multiclaimOnBehalf` distributions, from a reentrant or partially-completed prior `compound()` call, or (most directly) from any actor sending reward tokens straight to the `ManualCompound` contract address (no access control prevents third parties from transferring tokens to it). Given `compound()` is a fully public, unauthenticated entrypoint that any EOA can call, exploitation requires no special capital or privileged role — just calling `compound()` when a residual balance exists, which is realistic and repeatable.

### Recommendation
Track the balance of each `rewards[i].tokenAddress` immediately before calling `multiclaimOnBehalf`, and compute `receivedBalance` as the post-call balance minus the pre-call balance (the delta attributable to this specific `multiclaimOnBehalf` invocation), rather than using the raw `balanceOf(address(this))`. This ensures only the value produced by the current caller's own claim is distributed to them, and any pre-existing balance is left untouched (or handled via a separate, access-controlled sweep/accounting mechanism).

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, `ManualCompound`, a mock reward token, and a mock `SmartWomConvert`-style convertor; register the reward token via `addReward` with `_minRec` support.
2. Simulate a "residual balance" by directly transferring N tokens of a configured reward token to the `ManualCompound` contract address from an unrelated account (or by simulating a prior caller whose `multiclaimOnBehalf` produced tokens but whose downstream transfer step reverted, leaving balance stuck).
3. As Attacker (a fresh EOA with no claimable rewards, i.e., `_lps`/`_rewards` arrays produce zero claim from `multiclaimOnBehalf`), call `compound(_lps, _rewards, _convertRatio=0, _minRec=0, _lockMgp=false)`.
4. Assert: Attacker receives the full residual balance N via `IConverter.convertFor(..., msg.sender, ...)` or `safeTransfer`, despite having claimed 0 rewards themselves.
5. Differential check: Repeat the scenario where the legitimate owner of that residual balance calls `compound()` themselves — assert they receive 0 (funds already stolen by Attacker in step 3), demonstrating `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` no longer reconciles with the legitimate claimant's own share.

### Citations

**File:** rewards/ManualCompound.sol (L123-125)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
```

**File:** rewards/ManualCompound.sol (L139-159)
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
```
