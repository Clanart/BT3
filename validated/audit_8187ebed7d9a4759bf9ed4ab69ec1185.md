### Title
Reward-Map Key Collision Between CN `RewardAddr` and CL `CLPoolAddr` Silently Drops Staking Rewards - (File: kaiax/reward/impl/getter.go)

### Summary
`assignStakingRewards` / `assignStakingRewardsFlex` in [1](#0-0)  and [2](#0-1)  build a single `map[common.Address]*big.Int` (`alloc`) that is populated using two different, independently-controlled address namespaces as keys in the same map: each consolidated node's `RewardAddr` and (for Prague/CL-enabled nodes) its `CLStakingInfo.CLPoolAddr`. If any `CLPoolAddr` happens to equal another validator's `RewardAddr` (or another CN's `CLPoolAddr`), one entry silently overwrites the other in the Go map — exactly the `amountInShelter[lpToken] = amount` overwrite pattern from the referenced report, just applied to reward accounting instead of a shelter balance.

### Finding Description
`ConsolidatedNodes()` in [3](#0-2)  deduplicates `NodeIds`/`StakingContracts` by `RewardAddr`, guaranteeing `cn.RewardAddr` is unique across the returned `[]consolidatedNode`. However, it does **not** enforce that a `CLStakingInfo.CLPoolAddr` (sourced from `CLRegistry`/multicall, see [4](#0-3) ) is disjoint from the set of `RewardAddr`s or from other CNs' `CLPoolAddr`s.

In `assignStakingRewards`:
```
if isPrague && cn.CLStakingInfo != nil {
    cnAmount, clAmount := cn.Split(reward)
    alloc[cn.RewardAddr] = cnAmount
    alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount
} else {
    alloc[cn.RewardAddr] = reward
}
remaining.Sub(remaining, reward)
``` [5](#0-4) 

`remaining` is decremented by the full `reward` for every CN regardless of key collisions, but the `alloc` map only ever holds the **last write** for a colliding key. The identical pattern exists in `assignStakingRewardsFlex` [6](#0-5) .

The `alloc` map is then applied to the `RewardSpec` via a simple range loop that blindly credits whatever value is currently stored per address key:
```
for addr, amount := range stakersAlloc {
    spec.IncRecipient(addr, amount)
}
``` [7](#0-6) 

If `cn.CLStakingInfo.CLPoolAddr == otherCn.RewardAddr` (or two CLPoolAddrs collide), whichever entry is processed last in the deterministic `cns` slice iteration wins, and the other CN's/CL's rightful reward amount is never credited anywhere — it is neither paid to the intended recipient nor returned to the proposer as remainder (since `remaining` was already decremented for both amounts). The tokens are effectively burnt/lost from circulation for that block, differing from the value implied by `RewardConfig.MintingAmount`.

### Impact Explanation
This causes an unauthorized/incorrect reward outcome: a validator (or its consensus-liquidity pool) that should receive a portion of the block's staking reward receives nothing for that address, while the overall accounting (`remaining`/`stakers.Sub`) proceeds as if it had been correctly distributed — so the difference is not redirected to the proposer or any fund either. This is a silent token loss bug of the same class as the `amountInShelter` overwrite in the external report: a mapping keyed on attacker/operator-influenced addresses is overwritten instead of being isolated/accumulated per logical entity, permanently losing value that should have gone to a legitimate staker. Because the computation is deterministic given the on-chain `StakingInfo`, all honest nodes compute the same (wrong) result, so there is no chain split — the impact is confined to incorrect reward crediting rather than consensus divergence.

### Likelihood Explanation
Triggering this requires a CN's `RewardAddr` (set via `AddressBook`/`AddressBookV2` by a CN operator) to collide with a `CLPoolAddr` registered in `CLRegistry` for consensus liquidity (post-Prague). `CLPoolAddr` values are supplied by whoever registers a CL pool for a node; nothing in the reviewed reward/staking modules cross-validates that this address differs from existing CN reward addresses. A staker/operator managing multiple CNs and/or a CL pool registrant could reproduce this deterministically (accidentally by reusing an address across roles, or deliberately to grief another validator's payout), analogous to the "distracted/malicious admin" scenario the original report was judged Medium severity for. Likelihood is assessed as Medium: it requires a specific address-choice condition but no privileged/consensus-level access, only ordinary configuration transactions available to stakers/CL registrants.

### Recommendation
Do not share a single `map[common.Address]*big.Int` keyed by heterogeneous address roles (`RewardAddr` and `CLPoolAddr`). Instead:
- Use `alloc[addr] = new(big.Int).Add(alloc[addr], amount)` (accumulate) instead of `alloc[addr] = amount` (overwrite) in both `assignStakingRewards` and `assignStakingRewardsFlex`, mirroring the report's recommended `+=` fix, so that any accidental/intentional collision adds up rather than clobbers.
- Additionally, validate at the `StakingInfo`/`ConsolidatedNodes` layer that `CLPoolAddr` values are disjoint from all `RewardAddr`s and from each other, rejecting or flagging a `StakingInfo` snapshot that violates this invariant.

### Proof of Concept
1. Configure `AddressBook`/`AddressBookV2` so CN "A" has `RewardAddr = X` with staking amount sufficient to earn reward `Ra`.
2. Register a `CLStakingInfo` entry for another CN "B" (or for A itself acting maliciously through a colluding CL pool operator) in `CLRegistry` with `CLPoolAddr = X` (same as A's `RewardAddr`) and `CLStakingAmount` large enough to yield a nonzero `clAmount = Rb`.
3. At `FinalizeState` (deferred reward distribution) for a Prague-enabled block, `assignStakingRewards` iterates `cns`: when B (with `CLPoolAddr = X`) is processed after A, `alloc[X]` is overwritten from `Ra` (A's `cnAmount`) to `Rb` (B's `clAmount`) — or vice versa depending on iteration order — while `remaining` has already been reduced by both `Ra` and `Rb`.
4. Post-distribution, address `X` receives only one of `{Ra, Rb}` instead of `Ra` (as A's rightful reward), and the lost amount is absent from both the credited balances and the proposer's remainder in `specWithProposerAndFunds` [8](#0-7) , confirming the token loss.

### Citations

**File:** kaiax/reward/impl/getter.go (L363-365)
```go
	for addr, amount := range stakersAlloc {
		spec.IncRecipient(addr, amount)
	}
```

**File:** kaiax/reward/impl/getter.go (L421-484)
```go
// assignStakingRewardsFlex assigns staking rewards to stakers according to their staking amounts.
// Returns the allocation and the remainder.
func assignStakingRewardsFlex(config *reward.RewardConfig, budget *big.Int, si *staking.StakingInfo) (map[common.Address]*big.Int, *big.Int) {
	var (
		minStake  = config.MinimumStake.Uint64()
		threshold = config.StakingRewardThreshold.Uint64()
		isPrague  = config.Rules.IsPrague

		cns            = si.ConsolidatedNodes()
		excessInt      = make(map[common.Address]uint64)
		totalExcessInt = uint64(0)
	)

	// Calculate the excess stakes (the amount over the threshold) for each CN.
	for _, cn := range cns {
		// If the CNStaking is less than minStake, skip it. Even if (CNStaking + CLStaking) could be more than minStake,
		// the CNStaking alone must be at least minStake to be eligible.
		if cn.StakingAmount < minStake {
			continue
		}

		amount := cn.StakingAmount
		if isPrague && cn.CLStakingInfo != nil {
			amount += cn.CLStakingInfo.CLStakingAmount
		}

		// Excess is the amount over the threshold (not over minStake).
		if amount > threshold {
			excessInt[cn.RewardAddr] = amount - threshold
			totalExcessInt += excessInt[cn.RewardAddr]
		}
	}

	// Distribute the budget to the CNs based on the excess stakes.
	var (
		totalExcess = new(big.Int).SetUint64(totalExcessInt)
		remaining   = new(big.Int).Set(budget)
		alloc       = make(map[common.Address]*big.Int)
	)
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
}
```

**File:** kaiax/reward/impl/getter.go (L486-534)
```go
// assignStakingRewards assigns staking rewards to stakers according to their staking amounts.
// Returns the allocation and the remainder.
func assignStakingRewards(config *reward.RewardConfig, stakersReward *big.Int, si *staking.StakingInfo) (map[common.Address]*big.Int, *big.Int) {
	var (
		cns               = si.ConsolidatedNodes()
		minStake          = config.MinimumStake.Uint64()
		totalExcessInt    = uint64(0) // sum of excess stakes (the amount over minStake) over all stakers
		cnTotalStakingMap = make(map[common.Address]uint64)
		isPrague          = config.Rules.IsPrague
	)
	for _, cn := range cns {
		// If the CNStaking is less than minStake, skip it.
		if cn.StakingAmount >= minStake {
			// Calculate total staking amount once
			cnTotalStakingAmount := cn.StakingAmount
			if isPrague && cn.CLStakingInfo != nil {
				cnTotalStakingAmount += cn.CLStakingInfo.CLStakingAmount
			}
			totalExcessInt += cnTotalStakingAmount - minStake
			cnTotalStakingMap[cn.RewardAddr] = cnTotalStakingAmount
		}
	}

	var (
		totalExcess = new(big.Int).SetUint64(totalExcessInt)
		remaining   = new(big.Int).Set(stakersReward)
		alloc       = make(map[common.Address]*big.Int)
	)
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
		}
	}
	return alloc, remaining
}
```

**File:** kaiax/reward/impl/getter.go (L596-642)
```go
func specWithProposerAndFunds(spec *reward.RewardSpec, config *reward.RewardConfig, proposer, kif, kef *big.Int, si *staking.StakingInfo) *reward.RewardSpec {
	newSpec := spec.Copy()

	// If KIF or KEF address is not set, proposer takes it.
	if common.EmptyAddress(si.KIFAddr) {
		newSpec.KIF = common.Big0
		proposer.Add(proposer, kif)
	} else {
		newSpec.KIF = kif
		newSpec.IncRecipient(si.KIFAddr, kif)
	}

	if common.EmptyAddress(si.KEFAddr) {
		newSpec.KEF = common.Big0
		proposer.Add(proposer, kef)
	} else {
		newSpec.KEF = kef
		newSpec.IncRecipient(si.KEFAddr, kef)
	}

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

**File:** kaiax/staking/staking_info.go (L117-166)
```go
func (si *StakingInfo) ConsolidatedNodes() []consolidatedNode {
	if si.consolidatedNodes == nil {
		si.consolidatedNodes = si.consolidateNodes()
	}
	return *si.consolidatedNodes
}

func (si *StakingInfo) consolidateNodes() *[]consolidatedNode {
	// because Go map is not ordered, rList keeps track of the occurrence order of RewardAddrs.
	// to later arrange the consolidatedNodes.
	cmap := make(map[common.Address]*consolidatedNode)
	rList := make([]common.Address, 0, len(si.RewardAddrs))
	nToR := make(map[common.Address]common.Address)

	for i, n := range si.NodeIds {
		r := si.RewardAddrs[i]
		// Unique nodeId is guaranteed by AddressBook.
		nToR[n] = r
		if cn, ok := cmap[r]; ok {
			cn.NodeIds = append(cn.NodeIds, n)
			cn.StakingContracts = append(cn.StakingContracts, si.StakingContracts[i])
			cn.StakingAmount += si.StakingAmounts[i]
		} else {
			cmap[r] = &consolidatedNode{
				NodeIds:          []common.Address{n},
				StakingContracts: []common.Address{si.StakingContracts[i]},
				RewardAddr:       r,
				StakingAmount:    si.StakingAmounts[i],
			}
			rList = append(rList, r)
		}
	}

	// CLStakingInfo can only exist after Prague HF.
	if len(si.CLStakingInfos) > 0 {
		for _, clsi := range si.CLStakingInfos {
			// If the nodeId of CLStakingInfo is not found in nToR, it means the validator is not in the AddressBook.
			// So we skip it.
			if r, ok := nToR[clsi.CLNodeId]; ok {
				// One CLStakingInfo per validator is guaranteed by CLRegistry.
				cmap[r].CLStakingInfo = clsi
			}
		}
	}

	carr := make([]consolidatedNode, 0, len(cmap))
	for _, r := range rList {
		carr = append(carr, *cmap[r])
	}
	return &carr
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
