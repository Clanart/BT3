### Title
Unrestricted token sweep in `compound()` allows draining any stranded ERC20 balance, including tokens orphaned by `removeReward()` - (File: rewards/ManualCompound.sol)

### Summary
The "return non-compoundable rewards" loop in `compound()` transfers the contract's *entire current balance* of any address the caller lists in `_rewards[i][j]`, gated only by `!compoundableRewards[token]`, without ever checking that the token was actually claimed for the caller in this transaction via `multiclaimOnBehalf`. Combined with `removeReward()`, which flips `compoundableRewards[_tokenAddress] = false` without sweeping/handling any residual balance of that token still held by the contract, this lets any unprivileged caller drain leftover token balances that do not belong to them.

### Finding Description
`compound()` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)` and then iterates the caller-supplied `_rewards` array: [1](#0-0) 

For every `_rewards[i][j]` not present in `compoundableRewards`, it reads `IERC20(_rewards[i][j]).balanceOf(address(this))` and transfers the *whole* balance to `msg.sender`. This check only depends on the current mapping state and the raw contract balance — it does not verify that the tokens were actually paid out to the contract as part of the `multiclaimOnBehalf` call just made for `msg.sender`. Any token balance sitting in the contract for any reason (protocol dust, precision-loss remainders from `IConverter`/`ISimpleHelper` interactions, or accidental transfers) satisfies this condition and can be swept by an unrelated caller.

This is compounded by `removeReward()`: [2](#0-1) 

`removeReward` sets `compoundableRewards[_tokenAddress] = false` but performs no accounting or transfer of any balance of that token still held by `ManualCompound` at removal time. From that point on, `rewards[i].tokenAddress` no longer contains the token, yet `compoundableRewards` reconciliation is broken in the sense that any pre-existing balance of that token is now permanently reachable by the caller-controlled sweep in `compound()` — with `_minRec = 0` and an arbitrary `_convertRatio`/`_lockMgp`, since those parameters are irrelevant to loop 1's flow. An attacker can call `compound()` with any valid `_lps` entry (even one yielding zero new claims) and simply list the orphaned token address in `_rewards[i]` to have the full stranded balance sent to themselves, with no capital and no privileged role required.

I was not able to fully inspect `MasterMagpie.multiclaimOnBehalf` in `rewards/MasterMagpie.sol` within the available tool budget, so I cannot confirm whether it independently validates `_rewards[i][j]` against a pool's registered reward tokens before making external claim calls. However, that validation (if it exists) only affects whether *new* tokens get claimed into the contract during this call — it does not affect the described exploit, which targets balances the contract *already holds* before `compound()` is invoked (e.g., stranded post-`removeReward` dust), since the sweep loop uses `balanceOf(address(this))` unconditionally.

### Impact Explanation
Any unprivileged caller can drain any ERC20 balance stranded in `ManualCompound` — most concretely, funds left behind after an owner calls `removeReward()` on a token that still has a non-zero balance in the contract. Since that balance may represent unclaimed/unconverted yield that was accrued for the protocol's users before removal, this is a direct theft of those funds by an unrelated third party, matching Critical - Direct theft of user funds.

### Likelihood Explanation
Exploitation requires no special capital or privilege: the attacker only needs the contract to be holding a stray balance (via `removeReward()` leaving dust, precision-loss remainders from conversion/deposit operations, or any accidental transfer) and to call `compound()` naming that token. This is fully repeatable and requires no timing races with other users' transactions — it is a standing latent condition once any leftover balance exists.

### Recommendation
Do not use `balanceOf(address(this))` as the basis for the "return non-compoundable reward" sweep. Instead, have `multiclaimOnBehalf` return the exact amounts claimed per token/pool and only forward those specific claimed amounts back to `msg.sender`, rather than the contract's full standing balance. Additionally, `removeReward()` should sweep or otherwise properly account for any residual balance of the removed token (e.g., transfer it to the owner/treasury or require balance to be zero before allowing removal) so that no token balance is ever left both untracked by `compoundableRewards`/`rewards` and sweepable by an arbitrary caller.

### Proof of Concept
Hardhat test plan:
1. Deploy `ManualCompound` with a mock `masterMagpie`. Add a reward token `T` via `addReward` and mock a scenario where the contract accumulates a balance of `T` (e.g., simulate conversion leftover dust by directly minting `T` to the `ManualCompound` contract).
2. As owner, call `removeReward(index, T)` — verify `compoundableRewards[T] == false` while `IERC20(T).balanceOf(ManualCompound) > 0`.
3. From an unprivileged attacker EOA with zero prior interaction, call `compound(_lps, _rewards, _convertRatio=0, _minRec=0, _lockMgp=false)` where `_lps` is any valid pool array (can have empty per-pool reward sub-arrays to make `multiclaimOnBehalf` a no-op) and `_rewards[0] = [T]`.
4. Assert: attacker's balance of `T` increases by the full stranded amount, and `ManualCompound`'s balance of `T` goes to zero — despite the attacker never having claimed or been entitled to `T`.
5. Assert violation of invariant: a caller named an arbitrary/orphaned token and received the contract's entire balance of it, with no relationship between `compoundableRewards[T]` state and actual fund entitlement.

### Citations

**File:** rewards/ManualCompound.sol (L88-97)
```text
    function removeReward(uint256 _index, address _tokenAddress) validRewardIndex(_index) external onlyOwner {
        if(rewards[_index].tokenAddress != _tokenAddress) revert InvalidReward();
        for (uint i = _index; i < rewards.length - 1; i++) {
           rewards[i] = rewards[i+1];
        }
        rewards.pop();

        compoundableRewards[_tokenAddress] = false;
        emit RewardRemoved(_index, _tokenAddress);
    }
```

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
