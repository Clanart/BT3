## Analysis

The reported bug class — a **zero weight/denominator causing a division that reverts (or here, panics) and disrupts the reward/fund flow** — maps to a concrete Kaia analog in the reward distribution module's staking-liquidity split helper.

### Title
Division-by-zero panic in `consolidatedNode.Split` when a validator's combined CNStaking + CL staking amount is zero - ([File: kaiax/staking/staking_info.go])

### Summary
`consolidatedNode.Split`, used by the `kaiax/reward` module to proportionally split a reward amount between a validator's CNStaking (CN) and Consensus Liquidity (CL) pool, divides by `totalAmount = cnAmountBig + clAmountBig` without checking that the sum is non-zero. If both the validator's on-chain CN staking balance and its registered CL pool balance are zero at the same time (fully reachable through normal, unprivileged staking/unstaking activity), any block that must distribute a reward to that consolidated node causes a Go runtime panic (`big.Int` division by zero) during `FinalizeState()`/reward calculation — i.e., during ordinary block finalization that every node performs.

### Finding Description
`consolidatedNode.Split` is defined as: [1](#0-0) 

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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <-- divides by totalAmount, unchecked
	cnAmount := big.NewInt(0).Sub(amount, clAmount)
	return cnAmount, clAmount
}
```

It only guards against `CLStakingInfo == nil`, but never guards against `cnAmountBig + clAmountBig == 0`. `StakingAmount` reflects the CNStaking contract's actual KAIA balance and `CLStakingInfo.CLStakingAmount` reflects the CL pool's actual staked balance — both are ordinary, unprivileged economic quantities that any staker can drive to zero by fully unstaking/withdrawing, while the validator's `NodeId`/`RewardAddr` entry can remain in the AddressBook and CLRegistry (removal from those registries is a separate, later governance/admin action).

This `Split` function is invoked from two places in the reward getter with different levels of guarding:

1. `assignStakingRewardsFlex` / `assignStakingRewards` call `cn.Split(reward)` for CNs whose combined stake exceeds a governance-controlled threshold (`reward.minstake`/`reward.stakingrewardthreshold`), so the divide-by-zero here would need those thresholds effectively at 0: [2](#0-1) 

2. Critically, `specWithProposerAndFundsFlex` / `specWithProposerAndFunds` call `cn.Split(proposer)` on the **current proposer's own consolidated node with no staking-amount check at all** — only that `cn.RewardAddr == config.Rewardbase` and `cn.CLStakingInfo != nil`: [3](#0-2) [4](#0-3) 

So as long as the block proposer is a validator that has ever registered a CL pool (`CLStakingInfo != nil`) and both its CNStaking balance and CL pool balance are currently zero, every node that finalizes that proposer's block will panic in `Split`.

### Impact Explanation
This is reached deterministically inside `FinalizeState()` reward computation, which every full node (not just the proposer) executes when processing/verifying that block. A panic here crashes node processes network-wide for that block, halting the chain until a patched binary is deployed — a Critical/High availability impact directly analogous to the reported "funds definitely bricked" bug (a zero-weight edge case that is never validated against and permanently disables normal operation of the affected code path).

### Likelihood Explanation
Reaching this state does not require any privileged, malicious, or p2p-level action — a legitimate CN operator withdrawing their CNStaking balance to zero while their CL pool (registered once, e.g. via KIP-226 consensus liquidity) also happens to hold zero delegated stake (e.g., after CL delegators fully withdraw) is enough. Because Kaia validator/AddressBook membership does not automatically deregister a node when its balance hits zero, this state is realistically reachable by ordinary staking/unstaking operations from unprivileged stakers, and the proposer-path call in `specWithProposerAndFunds[Flex]` has no minimum-stake guard whatsoever.

### Recommendation
Add an explicit zero-check in `consolidatedNode.Split` (and/or before calling it) to avoid dividing by a zero `totalAmount`, e.g., return `(amount, big.NewInt(0))` (or route the full share to whichever side is non-zero) when `totalAmount.Sign() == 0`, mirroring how `generateProposerListWeighted` explicitly branches on `totalStakes > 0` before dividing: [5](#0-4) 

### Proof of Concept
1. Node `V` is registered in AddressBook (`NodeIds`/`StakingContracts`/`RewardAddrs`) and also has a `CLStakingInfo` entry in `CLStakingInfos` (registered once via CLRegistry).
2. `V`'s operator withdraws all funds from the CNStaking contract, and separately all CL delegators withdraw from `V`'s CL pool, so `StakingAmount == 0` and `CLStakingInfo.CLStakingAmount == 0` for `V`, while `V` remains listed in the AddressBook/CLRegistry (no admin removal yet).
3. `V` becomes/remains the block proposer for some block N (e.g. under RoundRobin `istanbul.policy`, proposer rotation is independent of stake amount).
4. During `FinalizeState()` for block N, `getDeferredRewardFullFlex`/`getDeferredRewardFullKore` calls `specWithProposerAndFundsFlex`/`specWithProposerAndFunds`, which finds `cn.RewardAddr == config.Rewardbase` for `V`, sees `cn.CLStakingInfo != nil`, and calls `cn.Split(proposer)`.
5. Inside `Split`, `totalAmount = 0 + 0 = 0`, and `clAmount.Div(clAmount, totalAmount)` panics with "division by zero", crashing every node processing block N.

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

**File:** kaiax/reward/impl/getter.go (L460-483)
```go
	for _, cn := range cns {
		if excessInt[cn.RewardAddr] <= 0 {
			continue
		}
		excess := new(big.Int).SetUint64(excessInt[cn.RewardAddr])

		// The KAIA unit will cancel out:
		// reward (kei) = excess (KAIA) * budget (kei) / totalExcess (KAIA)
		reward := new(big.Int).Div(new(big.Int).Mul(excess, budget), totalExcess)
		if reward.Sign() <= 0 {
			continue
		}

		// If Prague and CL is configured for this CN, split the reward between CN and CL.
		if isPrague && cn.CLStakingInfo != nil {
			cnAmount, clAmount := cn.Split(reward)
			alloc[cn.RewardAddr] = cnAmount
			alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount
		} else {
			alloc[cn.RewardAddr] = reward
		}
		remaining.Sub(remaining, reward)
	}
	return alloc, remaining
```

**File:** kaiax/reward/impl/getter.go (L566-592)
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

	newSpec.IncRecipient(config.Rewardbase, proposer)
	return newSpec
}
```

**File:** kaiax/reward/impl/getter.go (L616-642)
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

	newSpec.IncRecipient(config.Rewardbase, proposer)
	return newSpec
}
```

**File:** kaiax/valset/impl/getter_proposers.go (L237-251)
```go
	// Calculate percentile weights
	weights := make(map[common.Address]uint64)
	if totalStakes > 0 {
		for _, addr := range addrs {
			weight := uint64(math.Round(stakingAmounts[addr] * 100 / totalStakes))
			if weight <= 0 {
				weight = 1
			}
			weights[addr] = weight
		}
	} else {
		for _, addr := range addrs {
			weights[addr] = 0
		}
	}
```
