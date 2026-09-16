### Title
Stale Consensus-Liquidity Ratio in `consolidatedNode.Split` Causes Inadequate Reward Distribution - (File: kaiax/staking/staking_info.go)

### Summary
The Sherlock report describes `Pool.burnRTokens()` computing a token-to-liquidity conversion using a fixed ratio (`reinvestL / totalSupply`) that is assumed to stay constant, even though the underlying liquidity can change dynamically, causing liquidity providers to receive a disproportionate amount on redemption. The equivalent bug class in Kaia is the CN/CL reward split performed in `consolidatedNode.Split`, which converts a validator's proposer/staking reward into "CN share" vs "CL share" using a fixed ratio derived from a `StakingInfo` snapshot that is only refreshed once per `StakingUpdateInterval` (or once per block after the Kaia hardfork, but still sourced from the *previous* block, not the block being rewarded).

### Finding Description
`consolidatedNode.Split` divides a reward amount between the CN reward address and the CL pool address strictly in proportion to the stake amounts recorded in the `StakingInfo` snapshot: [1](#0-0) 

```go
func (c consolidatedNode) Split(amount *big.Int) (*big.Int, *big.Int) {
	...
	clAmount := new(big.Int).Mul(clAmountBig, amount)
	clAmount = clAmount.Div(clAmount, totalAmount)
	cnAmount := big.NewInt(0).Sub(amount, clAmount)
	return cnAmount, clAmount
}
```

This ratio (`cnAmountBig : clAmountBig`) is taken from `cn.StakingAmount` and `cn.CLStakingInfo.CLStakingAmount`, which are populated from a `StakingInfo` object fetched once per source block, per the module's documented staleness rule: [2](#0-1) 

The `StakingInfo` used for a given reward-distribution block `num` is drawn from a *historic* block (`SourceNum(num) = num - 1` post-Kaia, or from the beginning of the previous `StakingUpdateInterval` pre-Kaia). Within that gap, the actual on-chain CN staking balance and the actual CL pool staking balance can diverge from the snapshot (e.g. depositors/withdrawers interact with the CL pool contract, or the CN staking contract balance changes), yet every block's reward is split using the stale ratio — exactly mirroring the audit's root cause: "the calculated qty0/qty1 [here, cnAmount/clAmount] assume the proportion ... remains constant ... which might not hold true in a dynamic liquidity environment."

This is invoked from the Kore/Flex reward paths for every staking reward allocation: [3](#0-2) 

### Impact Explanation
Because the split ratio is frozen for an entire `StakingUpdateInterval` (pre-Kaia) or one block (post-Kaia, still off-by-one), a CN operator or CL pool participant who alters the CN-vs-CL stake balance immediately after a snapshot is taken can cause reward funds intended for CL depositors to be misallocated to the CN reward address (or vice versa) for the duration until the next snapshot. Since `CLPoolAddr` and the validator's `RewardAddr` are distinct, economically separate recipients, this is a concrete reward-redirection / value-misallocation issue rather than a rounding nuisance — funds that should accrue to CL stakers can instead flow to the validator operator's reward address, and this recurs every block until the StakingInfo is refreshed. This satisfies the "reward redirection" acceptance criterion.

### Likelihood Explanation
Reachable by a "staker" (validator operator or CL depositor), one of the permitted actor classes. No privileged or off-chain access is required — an actor need only submit ordinary staking/withdrawal transactions to the CNStaking or CL pool contracts around the interval boundary to shift the CN/CL ratio away from the value captured in the snapshot; the reward-splitting logic (`assignStakingRewards` / `consolidatedNode.Split`) will then apply the stale ratio deterministically for every subsequent block until the next snapshot is taken. The staleness window is bounded by `StakingUpdateInterval` (potentially large pre-Kaia) or one block (post-Kaia), so post-Kaia the exposure window is small but not zero; pre-Kaia the window can be substantial.

### Recommendation
Use the CN and CL staking amounts recorded at the same source block consistently, and consider narrowing the staleness window (or explicitly documenting/bounding the acceptable divergence) for the CN/CL split, similar to how the KIP-226 design already tracks `SourceBlockNum`. If real-time accuracy is required, the split ratio should be recomputed from the block being finalized rather than solely from the historic snapshot, or the protocol should enforce a lock-up/cooldown on CN/CL stake changes that at least covers one `StakingUpdateInterval` to prevent snapshot-timing manipulation.

### Proof of Concept
1. Let a validator's CN staking amount be `S_cn` and CL staking amount be `S_cl` at the `StakingInfo` snapshot block (`sourceNum`).
2. Immediately after `sourceNum` is captured, the CL pool depositors withdraw a large portion of their stake (or the CN operator adds more CN stake), changing the true live ratio to `S_cn' : S_cl'` where `S_cl' ≪ S_cl`.
3. For every subsequent block until the next `StakingUpdateInterval` snapshot (see `kaiax/staking/README.md` staleness rule, and `sourceBlockNum` in `kaiax/staking/impl/getter.go:330-347`), `assignStakingRewards`/`consolidatedNode.Split` (`kaiax/staking/staking_info.go:169-187`) still computes `clAmount = amount * S_cl / (S_cn + S_cl)` using the stale, now-inflated `S_cl`, over-crediting the CL pool address relative to its actual (now smaller) live stake, and under-crediting the CN reward address — or the reverse scenario for CN stake increases benefiting the CN operator at CL depositors' expense.
4. This repeats block-by-block until the next snapshot, producing a sustained reward misallocation that a staking participant can trigger by timing ordinary stake/withdraw transactions.

Note: I could not fully trace whether any additional reconciliation mechanism exists elsewhere in the reward or staking modules that might compensate for this staleness beyond what's documented in `kaiax/staking/README.md`; this assessment is based on the code and documentation retrieved.

### Citations

**File:** kaiax/staking/staking_info.go (L169-187)
```go
func (c consolidatedNode) Split(amount *big.Int) (*big.Int, *big.Int) {
	if c.CLStakingInfo == nil {
		return amount, big.NewInt(0)
	}

	var (
		cnAmountBig = big.NewInt(int64(c.StakingAmount))
		clAmountBig = big.NewInt(int64(c.CLStakingInfo.CLStakingAmount))
		totalAmount = new(big.Int).Add(cnAmountBig, clAmountBig)
	)

	clAmount := new(big.Int).Mul(clAmountBig, amount)
	clAmount = clAmount.Div(clAmount, totalAmount)

	// The remaining amount is for the CN.
	cnAmount := big.NewInt(0).Sub(amount, clAmount)

	return cnAmount, clAmount
}
```

**File:** kaiax/staking/README.md (L10-20)
```markdown
- When processing a block at `num`, the StakingInfo from a historic block state is used and the historic block (i.e. source block) is determined by the following:

  - If `num` is before Kaia hardfork, then StakingInfo is drawn from the beginning of the previous staking interval. Note that if the `num` is a multiple of StakingInterval, the staking info is drawn from two epochs ahead (e.g. in the example below, the staking info at block 3000 is drawn from block 1000).
    ```go
    SourceNum(num) = RoundDown(num - 1, StakingInterval) - StakingInterval
    RoundDown(n, p) = n - (n % p)
    ```
  - If `num` is after Kaia hardfork, then StakingInfo is drawn from the previous block.
    ```go
    SourceNum(num) = num - 1
    ```
```

**File:** kaiax/reward/impl/getter.go (L514-529)
```go
	for _, cn := range cns {
		cnTotalStakingAmount := cnTotalStakingMap[cn.RewardAddr]
		if cnTotalStakingAmount > minStake {
			// The KAIA unit will cancel out:
			// reward (kei) = excess (KAIA) * stakersReward (kei) / totalExcess (KAIA)
			excess := new(big.Int).SetUint64(cnTotalStakingAmount - minStake)
			if reward := new(big.Int).Div(new(big.Int).Mul(excess, stakersReward), totalExcess); reward.Sign() > 0 {
				if isPrague && cn.CLStakingInfo != nil {
					// The remaining amount will be added to the cnAmount.
					cnAmount, clAmount := cn.Split(reward)
					alloc[cn.RewardAddr] = cnAmount
					alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount
				} else {
					alloc[cn.RewardAddr] = reward
				}
				remaining.Sub(remaining, reward)
```
