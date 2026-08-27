### Title
`compound()` sweeps entire `balanceOf(address(this))` instead of the actual claimed delta, letting any unprivileged caller launder donated/dust reward tokens through `IConverter.convertFor`/`ILocker.lockFor` - (`rewards/ManualCompound.sol`)

### Summary
`ManualCompound.compound()` computes `receivedBalance = IERC20(_tokenAddress).balanceOf(address(this))` after calling `IMasterMagpie(masterMagpie).multiclaimOnBehalf(...)`, rather than tracking the delta actually produced by that specific claim call. Because `compound()` is `external` with no access control and accepts arbitrary (including empty) `_lps`/`_rewards` arrays, any address can pre-fund the contract with the compoundable reward token and then call `compound()` to have the entire balance — including tokens it never earned through `MasterMagpie` — routed to itself via `convertFor`/`lockFor`/`depositFor`.

### Finding Description [1](#0-0) 

The relevant flow:
1. `compound()` calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` which claims rewards for the caller and (based on the subsequent balance checks) transfers the claimed tokens into `ManualCompound` (`address(this)`).
2. For each configured compoundable reward, the contract does not diff a "before" snapshot against an "after" snapshot scoped to this call; it simply reads the live `balanceOf(address(this))`: [2](#0-1) 
3. That entire `receivedBalance` — regardless of its provenance — is then approved and pushed to `IConverter.convertFor`, `ILocker.lockFor`, or `ISimpleHelper.depositFor`, all keyed to `msg.sender`: [3](#0-2) 

Because `_lps` and `_rewards` can be empty arrays (a no-op claim) and there is no modifier restricting who can call `compound()` or requiring `_lps.length > 0`, an attacker can:
- Transfer (donate/airdrop) the compoundable reward token directly to `ManualCompound`'s address using a plain ERC20 `transfer`.
- Call `compound([], [], _convertRatio, _minRec, _lockMgp)`.
- The loop at line 139-160 will still find `receivedBalance > 0` for the donated token and route it to the attacker via `convertFor`/`lockFor`, even though the attacker claimed nothing from `MasterMagpie`.

This also means any residual/dust balance left in the contract (from partial claims, rounding, or a stuck transfer) is swept in full by whichever unprivileged address happens to call `compound()` next — the contract holds no per-user accounting to prevent this.

Note: because the reward token that is swept is the same token the attacker (or a prior party) actually deposited into the contract, the downstream mint/lock in `IConverter`/`ILocker` is still collateralized by real tokens transferred in the same call — this is not a supply-inflation/insolvency bug for the destination vault. The concrete, provable impact is theft of any token balance sitting in `ManualCompound` that is not the caller's own legitimate claim (donations/dust/stuck transfers), since `compound()` provides no linkage between `msg.sender`'s actual `MasterMagpie` entitlement and the amount it forwards on their behalf.

### Impact Explanation
Any unprivileged address can misappropriate reward tokens that are physically present in `ManualCompound` but not attributable to their own `MasterMagpie` claim (e.g., accidental sends, dust, or tokens left by a previous partially-processed call), converting/locking them to their own benefit. This falls under "theft of funds mistakenly/directly sent to the contract" and "theft of unclaimed yield" if any legitimate dust belonging to the protocol/other stakers is present at call time. The magnitude is bounded by whatever balance is sitting in the contract at call time, which is typically small/opportunistic rather than a systemic insolvency vector, since the attacker must supply real tokens for any large-value exploitation.

### Likelihood Explanation
Trivial to execute: a plain ERC20 `transfer` to a known, non-upgradeable contract address followed by a permissionless call to `compound()` with empty or arbitrary arrays. No special privileges, flash loans, or timing races are required. It is fully repeatable and the attacker can front-run/back-run any moment when dust or a stray transfer exists in the contract's balance.

### Recommendation
Track the actual amount claimed per call by snapshotting `balanceOf(address(this))` immediately before calling `multiclaimOnBehalf` and using the post-call delta (`balanceAfter - balanceBefore`) as `receivedBalance`, rather than the raw live balance. Additionally, consider requiring `_lps.length > 0` (reject no-op calls) and adding a sweep/rescue function restricted to the owner for handling any tokens accidentally sent to the contract, so that stray balances cannot be opportunistically claimed by arbitrary callers.

### Proof of Concept
Foundry test plan:
1. Deploy `ManualCompound` with a mock `MasterMagpie`, mock compoundable reward token (e.g., mock WOM), and a mock `IConverter`/`ILocker` that records `(amount, recipient)` passed to `convertFor`/`lockFor`.
2. Configure the reward via `addReward` (as owner in test setup) with the mock convertor/locker set.
3. From an attacker EOA with zero stake in `MasterMagpie`, mint/transfer `1000e18` of the reward token directly to the `ManualCompound` contract address.
4. Attacker calls `compound(new address[](0), new address[][](0), _convertRatio, _minRec, false)`.
5. Assert that the mock `MasterMagpie.multiclaimOnBehalf` transferred `0` tokens (attacker had no claimable rewards), yet `IConverter.convertFor` (or `ILocker.lockFor`) was invoked with `amount == 1000e18` and `recipient == attacker`.
6. Assert this amount does not equal the attacker's actual claimable balance from `MasterMagpie` (which is `0`), demonstrating that `receivedBalance` is derived from `balanceOf(address(this))` rather than the caller's real entitlement.

### Citations

**File:** rewards/ManualCompound.sol (L123-163)
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

        emit Compounded(msg.sender, rewardTokensLength, _lockMgp);
    }
```
