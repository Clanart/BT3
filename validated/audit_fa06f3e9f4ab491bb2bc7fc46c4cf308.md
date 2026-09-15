Confirmed: this is a valid analog — the same bug class ("division based on a value that can legitimately be zero at the time of the computation, bypassing an intended safety check / causing incorrect state") is reachable in the reward-distribution path via `consolidatedNode.Split`, invoked unconditionally from `specWithProposerAndFunds` (and similarly `specWithProposerAndFundsFlex`) for the block proposer, without first verifying that the proposer's consolidated stake is non-zero.

### Title
Division-by-zero panic / unguarded stake split in proposer reward distribution when CN staking amount and CL staking amount are both zero - (File: kaiax/reward/impl/getter.go)

### Summary
`specWithProposerAndFunds` and `specWithProposerAndFundsFlex` locate the proposer's `consolidatedNode` and, if it has a `CLStakingInfo`, unconditionally call `cn.Split(proposer)` to divide the proposer reward between the CN reward address and the CL pool address. Unlike `assignStakingRewards`/`assignStakingRewardsFlex` (which only invoke `Split` for nodes already filtered to have `StakingAmount >= minStake`, guaranteeing a non-zero denominator), the proposer-path calls `Split` with no minimum-stake gating at all.

### Finding Description
`consolidatedNode.Split` computes:
```
totalAmount = cnAmountBig(StakingAmount) + clAmountBig(CLStakingAmount)
clAmount = amount * clAmountBig / totalAmount
``` [1](#0-0) 

If `totalAmount == 0` this is an integer division by zero in Go, which panics. The proposer-reward call site does not check that the CN's `StakingAmount` (nor the CL's `CLStakingAmount`) is non-zero before calling `Split`; it only checks that `cn.CLStakingInfo != nil`: [2](#0-1) 

This mirrors the reported Yeet flaw: a computation (`_minimumYeetPoint`/here, `Split`'s ratio) is derived from a quantity (`totalPot`/here, `totalAmount = StakingAmount + CLStakingAmount`) that is not guaranteed to be non-zero at the moment the guarded logic executes, because the upstream data source (AddressBook / CLRegistry) can legitimately report a staking amount of 0 for a registered node: `stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()` with no lower-bound check, and CN registration in the AddressBook does not require any minimum stake to be listed as a node/reward address. [3](#0-2) [4](#0-3) 

Whereas the staker-reward allocation path is correctly guarded — `Split` is only reached when `cnTotalStakingAmount > minStake` (implying a non-zero denominator) — the proposer path has no such guard: [5](#0-4) 

### Impact Explanation
If the current block's proposer is a CN whose `StakingAmount == 0` (e.g., a newly onboarded, permissionless, or under-staked validator that is nevertheless a valid round-robin/committee proposer) and it has a registered `CLStakingInfo` with `CLStakingAmount == 0` (freshly-registered CL pool with no liquidity staked yet), then `FinalizeState`/`GetBlockReward` for that block will panic inside `Split` while computing the deferred reward. Since every full node must execute the identical reward-distribution logic to finalize/verify the block, this is a deterministic state-transition crash reachable by ordinary on-chain configuration (registering a CN with a CL pool but not yet staking into it), not a network-layer or peer-message bug. This can halt block finalization / reward calculation across all conformant nodes processing that block, which is a state-transition-integrity issue distinct from network DoS.

### Likelihood Explanation
Requires: (1) Prague hardfork active, (2) the proposer's reward address has an associated `CLStakingInfo` (attainable by registering a CL pool via CLRegistry for that node without depositing any CL stake), and (3) the CN's own `StakingAmount` in the AddressBook is 0 or otherwise the sum with CL amount is 0. Because CN registration and CL pool registration are both permissionless/administrative actions not inherently tied to a minimum-stake requirement at registration time (only reward eligibility for the *staker* pool is gated by `minStake`, per `assignStakingRewards`), this condition can arise from ordinary operational sequencing (e.g., a node registers itself and a CL pool before either stakes any KAIA, and is still selected as proposer under round-robin).

### Recommendation
Guard the proposer's `Split` call the same way the staker-allocation path is guarded: verify `cn.StakingAmount + cn.CLStakingInfo.CLStakingAmount > 0` (or explicitly `> minStake`/some floor) before calling `cn.Split(proposer)` in both `specWithProposerAndFunds` and `specWithProposerAndFundsFlex`; if the total is zero, skip the split and assign the entire proposer reward to `cn.RewardAddr` directly, analogous to how `Split` itself returns `(amount, 0)` when `CLStakingInfo == nil`.

### Proof of Concept
1. Enable Prague hardfork.
2. Register a validator node N in the AddressBook with `StakingAmount = 0` (no CNStaking deposit) and reward address R.
3. Register a CL pool for N via CLRegistry with `CLStakingAmount = 0` (pool created, no stake deposited yet).
4. Arrange for N (via reward address R) to be selected as block proposer for some block (e.g., under round-robin proposer policy).
5. When `GetBlockReward`/`FinalizeState` computes the deferred reward for that block, `specWithProposerAndFunds` locates the consolidated node for R, finds `cn.CLStakingInfo != nil`, and calls `cn.Split(proposer)`, where `totalAmount = 0 + 0 = 0`, causing a division-by-zero panic in `consolidatedNode.Split` (`kaiax/staking/staking_info.go:180-181`), crashing block processing on every node executing this path.

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

**File:** kaiax/reward/impl/getter.go (L514-530)
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
			}
```

**File:** kaiax/reward/impl/getter.go (L616-638)
```go
	newSpec.Proposer = proposer
	if !config.Rules.IsPrague || si.CLStakingInfos == nil {
		newSpec.IncRecipient(config.Rewardbase, proposer)
		return newSpec
	}

	// Handle CLStakingInfo for proposer after Prague
	cns := si.ConsolidatedNodes()
	for _, cn := range cns {
		if cn.RewardAddr != config.Rewardbase {
			continue
		}
		if cn.CLStakingInfo == nil {
			// Early exit if there's no CL for proposer
			break
		}

		cnAmount, clAmount := cn.Split(proposer)

		newSpec.IncRecipient(cn.RewardAddr, cnAmount)
		newSpec.IncRecipient(cn.CLStakingInfo.CLPoolAddr, clAmount)
		return newSpec
	}
```

**File:** kaiax/staking/impl/getter.go (L211-221)
```go
	var clStakingInfos staking.CLStakingInfos
	if len(clRes.NodeIds) > 0 {
		clStakingInfos = make(staking.CLStakingInfos, len(clRes.NodeIds))
		for i := range clRes.NodeIds {
			clStakingInfos[i] = &staking.CLStakingInfo{
				CLNodeId:        clRes.NodeIds[i],
				CLPoolAddr:      clRes.ClPools[i],
				CLStakingAmount: big.NewInt(0).Div(clRes.StakingAmounts[i], big.NewInt(params.KAIA)).Uint64(),
			}
		}
	}
```

**File:** kaiax/staking/impl/getter.go (L286-288)
```go
	for i, a := range amounts {
		stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()
	}
```
