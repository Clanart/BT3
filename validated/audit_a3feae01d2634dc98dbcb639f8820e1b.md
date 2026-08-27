### Title
Non-compoundable reward sweep in `ManualCompound.compound()` drains the contract's entire token balance instead of only the caller's freshly-claimed amount - ([File: rewards/ManualCompound.sol])

### Summary
`ManualCompound.compound()` lets the caller freely choose the `_rewards` token addresses that are checked against the global `compoundableRewards` mapping, and for any address that is not a registered compoundable reward it transfers `IERC20(token).balanceOf(address(this))` — the entire contract balance — to `msg.sender`. Because this sweep uses the contract-wide balance rather than the amount actually received for `msg.sender` in that transaction, any residual balance of that token sitting in the contract (e.g., left over from another user's earlier, incompletely swept `compound()` call) is stolen by whoever next references that token address in `_rewards`, with no check that the token corresponds to `_lps[i]` or to the caller's own accrual.

### Finding Description
In `compound()`: [1](#0-0) 

`_lps` and `_rewards` are fully attacker-supplied, and `multiclaimOnBehalf(_lps, _rewards, msg.sender)` claims/credits rewards strictly for `msg.sender`'s own stake in the given `_lps` — the attacker cannot make MasterMagpie transfer a victim's accrued rewards to `ManualCompound` on the attacker's behalf. However, after that call, the loop at lines 127-138 does not verify that `_rewards[i][j]` is a reward token that actually belongs to `_lps[i]`, nor does it check that the amount swept originated from the current `multiclaimOnBehalf` call. It simply looks up the global `compoundableRewards[_rewards[i][j]]` flag and, if false, transfers out `IERC20(_rewards[i][j]).balanceOf(address(this))` in full.

Because the contract keeps no per-user or per-transaction accounting of "amount just received" vs. "pre-existing balance," any non-compoundable token balance that is sitting in the contract for any reason (e.g., a previous user's `compound()` call that claimed a reward token not included in their own `_rewards` list, or any other leftover) is entirely swept to the next caller who simply references that same token address, paired with an arbitrary/unrelated `_lps` entry. The `compoundableRewards` mapping is global and has no linkage back to `_lps[i]`, so the attacker fully controls which token gets drained independent of which LP they pass.

### Impact Explanation
This results in theft of other users' unclaimed/queued reward-token balances held in `ManualCompound`, matching the "theft of unclaimed yield" impact class. The stolen amount is bounded by whatever non-compoundable reward balance happens to reside in the contract at call time.

### Likelihood Explanation
Exploitability requires only that a residual balance of some non-compoundable reward token exist in `ManualCompound` (stated as a given precondition) and that the attacker be a normal staker able to call `compound()` with a crafted `_rewards` array containing that token address — no privileged role is required, and the attack is repeatable each time residual balances accumulate.

### Recommendation
Track the amount actually received for `msg.sender` during the current `multiclaimOnBehalf` call (e.g., via balance-before/after delta scoped to this transaction) rather than sweeping the full `balanceOf(address(this))`, and validate that each `_rewards[i][j]` entry is part of the actual reward-token set returned for `_lps[i]` before sweeping or converting it.

### Proof of Concept
Foundry test outline:
1. Deploy `ManualCompound` with `masterMagpie` mocked, and register no reward config for `tokenX` (so `compoundableRewards[tokenX] == false`).
2. Seed `ManualCompound` with a residual `tokenX` balance representing a victim's leftover claimed-but-unswept reward (simulate via a prior mocked `compound()` call by the victim that claims `tokenX` without including it in the victim's own `_rewards` array, leaving it unswept).
3. As attacker (unrelated stake), call `compound(lps=[attackerUnrelatedLp], _rewards=[[tokenX]], 0, 0, false)` where the mocked `MasterMagpie.multiclaimOnBehalf` returns/transfers zero `tokenX` for the attacker's own accrual.
4. Assert attacker's `tokenX` balance after the call equals the full residual balance that was seeded for the victim, proving the non-compoundable branch swept the shared contract balance rather than only the attacker's entitled accrual.

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
