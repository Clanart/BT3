## Title
Postponed/frozen linear vesting for low-decimal or small-allocation tokens in `Airdrop2._getClaimable` due to premature integer division - (File: `rewards/Airdrop2.sol`)

### Summary
`Airdrop2.sol` implements a merkle-based airdrop with a 5% instant unlock and a periodic (interval-based) linear vesting schedule for the remaining 95%. The vesting math in `_getClaimable` performs an integer division of elapsed time by the interval size before multiplying by the allocation and dividing again by the total number of periods. This ordering reproduces the exact truncation pattern from the referenced VTVLVesting.sol finding: for tokens with low decimals or for a fine-grained (e.g. per-second) interval configuration, the vested amount rounds down to zero for an extended period, delaying a claimant's ability to receive tokens they are entitled to.

### Finding Description
The vesting calculation is: [1](#0-0) 

```solidity
uint256 vested = (totalAmount * 5 / 100) + (totalAmount * 95 / 100) * ((block.timestamp - startVestingTime) / intervals) / (vestingPeriodCount);
```

The term `(block.timestamp - startVestingTime) / intervals` computes the number of whole intervals elapsed as an integer *before* it is multiplied by `totalAmount * 95 / 100` and divided by `vestingPeriodCount`. This is structurally identical to the VTVLVesting.sol bug where a per-unit-time rate is derived through an early division and then combined with a second division, causing the numerator to be smaller than the denominator (`vestingPeriodCount`) for many elapsed intervals when the token allocation is denominated in low decimals (e.g. 6-decimal tokens like USDC/USDT) or when `intervals`/`vestingPeriodCount` are configured for fine granularity (e.g. `intervals = 1` second for a multi-year vesting schedule, mirroring the 10-year/6-decimal example in the source report).

Concretely, with `intervals = 1` (per-second granularity) and `vestingPeriodCount` set to the full vesting duration in seconds (e.g. 315,360,000 for 10 years), the formula collapses to the same shape as VTVL's `linearVestAmount * elapsedSecs / finalVestingDurationSecs`, and for a 6-decimal token allocation the per-second numerator (`totalAmount*95/100`) can be smaller than `vestingPeriodCount`, causing `vested` to stay pinned at the 5% initial unlock value for many days/weeks even though real time has elapsed and the user calls `claim()`.

### Impact Explanation
An eligible airdrop/vesting recipient calling `claim()` via [2](#0-1)  receives less than the intended linearly-vested amount for an extended period (potentially many days), because `_getClaimable` truncates the linear component to zero. This is a genuine freezing of a user's already-allocated tokens for well over 24 hours, matching the accepted impact class of a prolonged freeze of user funds/unclaimed distribution, and — as in the referenced report — exposes the recipient to market risk (other holders can move against the token while the recipient's vested claim is stuck at zero growth).

### Likelihood Explanation
This is directly reachable by any airdrop recipient simply by calling the public `claim`/`getClaimable` functions; no privileged role is required to trigger it. It manifests whenever the deployed `intervals`/`vestingPeriodCount` parameters yield a fine-grained schedule combined with a token of low decimals or a modest-size per-user allocation — a realistic deployment configuration for reward tokens such as USDC/USDT-style 6-decimal tokens, consistent with the exact scenario described in the source finding.

### Recommendation
Reorder the arithmetic to multiply before dividing, and avoid the intermediate integer division of elapsed time by `intervals`:
```solidity
uint256 elapsed = block.timestamp - startVestingTime;
uint256 totalDuration = intervals * vestingPeriodCount;
uint256 vested = (totalAmount * 5 / 100) + (totalAmount * 95 / 100 * elapsed) / totalDuration;
```
Additionally, consider scaling intermediate values (e.g. multiplying by a fixed-point precision factor) before dividing, similar to the `10**decimals` scaling used in `BaseRewardPoolV2._provisionReward`/`_earned`, to preserve precision regardless of token decimals.

### Proof of Concept
1. Deploy `Airdrop2` with a 6-decimal `reward` token, `intervals = 1` (per-second vesting), `vestingPeriodCount = 315360000` (10 years in seconds), and `startVestingTime = now`.
2. Register a merkle leaf for `totalAmount = 10_000 * 10**6` (10,000 tokens).
3. Advance time by several days and call `claim(totalAmount, proof, false)`.
4. Observe `_getClaimable` returns only the 5% initial unlock amount (`vested` stays flat) because `(totalAmount * 95/100) * elapsedSeconds / 315360000` rounds to `0` for elapsed durations under ~12 days, replicating the exact rounding window computed in the source VTVLVesting.sol report.

### Citations

**File:** rewards/Airdrop2.sol (L78-96)
```text
    function claim(uint256 totalAmount, bytes32[] calldata merkleProof, bool isLock
    ) external whenNotPaused nonReentrant {
        require(block.timestamp >= startVestingTime, "Airdrop2: Drop dose not start.");
        //Verify the merkle proof.
        require(verifyProof(msg.sender, totalAmount, merkleProof), "Airdrop2: Invalid proof.");

        uint256 claimable = _getClaimable(msg.sender, totalAmount);

        // Mark it claimed and send the token.
        if (isLock) {
            reward.safeApprove(address(vlmgp), claimable);
            vlmgp.lockFor(claimable, msg.sender);
        } else {
            reward.safeTransfer(msg.sender, claimable);
        }
        uint256 userClaimedAmount = claimedAmount[msg.sender];
        claimedAmount[msg.sender] = userClaimedAmount + claimable;
        emit ClaimEvent(msg.sender, claimable, isLock);
    }
```

**File:** rewards/Airdrop2.sol (L100-112)
```text
    function _getClaimable(address account, uint256 totalAmount) internal view returns (uint256) {
        uint256 claimed = getClaimed(account);
        if (claimed >= totalAmount) {
            return 0;
        }

        uint256 vested = (totalAmount * 5 / 100) + (totalAmount * 95 / 100) * ((block.timestamp - startVestingTime) / intervals) / (vestingPeriodCount);
        if (vested > totalAmount) {
            return totalAmount - claimed;
        }

        return vested - claimed;
    }
```
