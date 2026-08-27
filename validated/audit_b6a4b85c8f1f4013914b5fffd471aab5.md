## Title
Arbitrary ERC20 token balance can be scooped from `ManualCompound` via attacker-chosen reward token addresses - (File: rewards/ManualCompound.sol)

### Summary
`ManualCompound.compound()` lets the caller supply an arbitrary `address[][] _rewards` argument. For any token address in that array which is not registered as a "compoundable" reward, the function unconditionally sweeps the **entire balance** of that token held by the `ManualCompound` contract to `msg.sender`, without any check that the token is actually related to the pools being claimed or that the caller is entitled to it. This mirrors the reported `settleAuction()` bug class, where a function accepts a caller-supplied token address and blindly transfers whatever balance of that token the contract holds.

### Finding Description
`compound()` first calls `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, which claims rewards from the underlying `BaseRewardPool`s for the reward tokens specified in `_rewards`. After that call returns, the function iterates the same caller-supplied `_rewards` array and, for tokens not present in the `compoundableRewards` mapping, transfers the contract's **full current balance** of that token to `msg.sender`: [1](#0-0) 

```solidity
function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
    ...
    IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
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
    ...
}
```

The transfer amount is `IERC20(token).balanceOf(address(this))` — the whole contract balance of the specified token — not the amount actually claimed for that specific caller from that specific pool. `_rewards[i][j]` is fully attacker-controlled and is only constrained by `_lps.length == _rewards.length` inside `MasterMagpie._multiClaim` [2](#0-1) ; it is not validated to be a token that is actually a registered reward for pool `_lps[i]` before this sweep logic runs. Any address (an unrelated airdropped token, dust left in the contract, or even a leftover balance of another user's not-yet-forwarded reward due to rounding) can be named here and drained by any caller who invokes `compound()`.

This is directly analogous to the reported `settleAuction()` issue: a public/external function accepts a user-supplied token address and transfers out whatever balance of that token the contract currently holds, with no check that this token belongs to the set the contract is supposed to manage on the caller's behalf.

### Impact Explanation
If `ManualCompound` accumulates any ERC20 balance that is not a "compoundable" reward token (e.g., mis-sent tokens, airdrops, or reward-token dust from partial harvest/claim rounding), any ordinary wallet can call `compound()` with a crafted `_rewards` array to redirect that entire balance to itself. This is a direct theft of funds/yield sitting in the contract, exploitable by any unprivileged wallet with no special role required.

### Likelihood Explanation
The function is `external` and callable by anyone; no owner/role check gates the sweep branch. The only prerequisite is that the `ManualCompound` contract holds a non-compoundable token balance greater than zero, which is plausible via airdrops, accidental transfers, or reward accounting dust — a low bar to trigger.

### Recommendation
Do not use the contract's total token balance as the transfer amount for non-compoundable rewards. Instead, track and transfer only the amount actually received/claimed during this specific `multiclaimOnBehalf` call (e.g., by measuring balance-before/balance-after deltas scoped to this invocation), and/or restrict `_rewards[i][j]` to a known/registered reward-token set per pool before allowing any balance-based transfer.

### Proof of Concept
1. Some non-compoundable ERC20 token `X` ends up held by the `ManualCompound` contract (airdrop, mistaken transfer, or accumulated reward dust).
2. An attacker calls `compound(_lps, _rewards, ...)` with `_lps` containing any valid pool and `_rewards[i]` containing token `X`'s address (with `compoundableRewards[X] == false`).
3. `multiclaimOnBehalf` executes normally (claims whatever legitimate rewards, if any, for the specified pools/tokens) [3](#0-2) .
4. The loop in `compound()` then checks `!compoundableRewards[X]`, reads `IERC20(X).balanceOf(address(this))`, and transfers that **entire balance** to `msg.sender` [4](#0-3) , regardless of whether the attacker had any legitimate claim on token `X`.

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

**File:** rewards/MasterMagpie.sol (L420-424)
```text
    function multiclaimOnBehalf(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused _onlyCompounder
    {
        _multiClaim(_stakingTokens, _account, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L536-538)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();
```
