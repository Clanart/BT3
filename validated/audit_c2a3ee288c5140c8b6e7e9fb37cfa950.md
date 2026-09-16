### Title
Division by Zero Panic (Chain Halt) in Consensus-Liquidity Reward Split — (File: `kaiax/staking/staking_info.go`)

### Summary
`consolidatedNode.Split()` divides by `totalAmount = cnAmountBig + clAmountBig` without checking whether this sum is zero. This function is invoked unconditionally during block finalization reward distribution (`FinalizeState`) whenever a validator's consolidated CN entry has a non-nil `CLStakingInfo`, regardless of whether the CN's own staking amount (`StakingAmount`) is non-zero. If a validator's CN staking amount is `0` (e.g., a node still present in the AddressBook/validator set but its CN stake has been effectively withdrawn/reduced to zero, which the code path does not prevent) while it still has a registered `CLStakingInfo` with `CLStakingAmount == 0`, `totalAmount` becomes zero, causing a `big.Int` division-by-zero panic.

### Finding Description
`consolidatedNode.Split` at [1](#0-0)  computes:
```go
cnAmountBig = big.NewInt(int64(c.StakingAmount))
clAmountBig = big.NewInt(int64(c.CLStakingInfo.CLStakingAmount))
totalAmount = new(big.Int).Add(cnAmountBig, clAmountBig)
clAmount := new(big.Int).Mul(clAmountBig, amount)
clAmount = clAmount.Div(clAmount, totalAmount)   // <-- panics if totalAmount == 0
```
This is analogous to the TensorFlow `MaxPoolGradWithArgmax` bug: a divisor derived from external/attacker-influenced input is used without validating it is non-zero.

This function is called from the reward finalization path `specWithProposerAndFundsFlex`, which — critically — does **not** gate the call on `cn.StakingAmount` being non-zero or above any minimum; it only checks `cn.CLStakingInfo != nil`: [2](#0-1) 
```go
cns := si.ConsolidatedNodes()
for _, cn := range cns {
    if cn.RewardAddr != config.Rewardbase {
        continue
    }
    if cn.CLStakingInfo == nil {
        break
    }
    cnAmount, clAmount := cn.Split(proposer)   // no minStake / non-zero check here
    ...
}
```
By contrast, the other two call sites (`assignStakingRewardsFlex` and `assignStakingRewards`) only call `cn.Split` after already establishing `cn.StakingAmount >= minStake` (implying a non-zero, bounded total), so those paths are safe: [3](#0-2) [4](#0-3) 

The proposer-reward path in `specWithProposerAndFundsFlex` lacks this guard. `StakingInfo` (including `StakingAmount` and `CLStakingInfo`) is populated from on-chain contract state (`AddressBook`/`AddressBookV2` and the CL `Registry`) as seen in [5](#0-4) , so a CN's staking amount rounds down to whole KAIA units (`Div(amounts[i], KAIA)`), meaning any staking-contract balance below 1 KAIA is truncated to `0` while the node can still remain registered as a council/validator entry with a reward address. If that validator also has a `CLStakingInfo` entry whose pool currently holds zero delegated stake (`CLStakingAmount == 0`, e.g., before any staker joins the pool, or after all CL stakers withdraw), and that validator becomes the block proposer under the Prague/flex reward rule, `cn.Split(proposer)` divides by zero.

### Impact Explanation
A `big.Int.Div` division by zero in Go panics. Because this code executes inside block state-transition finalization (`FinalizeState`, called by every full node processing the block), a panic here is deterministic and reproducible on every node that processes the offending block — this is a **chain-halt / consensus-wide denial of service**, not a localized crash. Unlike a normal node crash from bad input, this occurs during normal block production/validation by any node in the network once the triggering on-chain state (near-zero CN stake + near-zero/zero CL pool stake) and proposer rotation align, causing the entire network to stall until the state is manually patched. This satisfies the "state divergence/chain halt" impact bar required by the validation rules.

### Likelihood Explanation
Likelihood is Medium: it requires the Prague hardfork + flex reward rule to be active (`UseFlexReward`, `istanbul.policy==2`, `reward.deferredtxfee=true`) and a specific staking configuration (a council node's CN stake truncating to 0 KAIA while still being a registered/reward-eligible validator with an associated but empty CL pool) to coincide with that validator's proposer turn. This does not require an unprivileged transaction sender directly, but is reachable through ordinary validator/staking lifecycle operations (unstaking down toward zero, or a freshly-created CL pool with no delegators yet) that any staking-address holder can trigger — no privileged/malicious-node access is needed to create the zero-stake condition, since staking amount changes are just regular contract calls (unstake, CL pool creation) reflected on-chain.

### Recommendation
Add an explicit non-zero, guarded division in `consolidatedNode.Split`:
```go
if totalAmount.Sign() <= 0 {
    return amount, big.NewInt(0) // or return an error and treat CN as ineligible
}
```
Additionally, harden the caller in `specWithProposerAndFundsFlex` (and any future call sites) to only invoke `cn.Split` when the combined CN+CL stake is verified non-zero, mirroring the guard already present in `assignStakingRewardsFlex`/`assignStakingRewards`.

### Proof of Concept
1. Enable Prague hardfork with `istanbul.policy=2`, `reward.deferredtxfee=true`, `reward.useflexreward=true`.
2. Have a council node (validator) reduce its CN staking-contract balance to below 1 KAIA (rounds to `StakingAmount = 0` per [6](#0-5) ) while remaining a registered AddressBook validator/reward-eligible entry.
3. Register (or leave freshly created) a CL pool for that same validator's `NodeId` in the CL `Registry` with zero delegated stake, so `CLStakingInfo.CLStakingAmount == 0`.
4. Wait until this validator is selected as block proposer.
5. During `FinalizeState`, `getDeferredRewardFullFlex` → `specWithProposerAndFundsFlex` finds this validator's consolidated node (`cn.RewardAddr == config.Rewardbase`), sees `cn.CLStakingInfo != nil`, and calls `cn.Split(proposer)`. With `cn.StakingAmount == 0` and `cn.CLStakingInfo.CLStakingAmount == 0`, `totalAmount` in [7](#0-6)  is `0`, causing `clAmount.Div(clAmount, totalAmount)` to panic — crashing every node processing this block.

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

**File:** kaiax/reward/impl/getter.go (L514-533)
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
		}
	}
	return alloc, remaining
```

**File:** kaiax/reward/impl/getter.go (L572-588)
```go
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

**File:** kaiax/staking/impl/getter.go (L184-234)
```go
func parsePermissionlessCallResult(num uint64, profiles []multicall.Profile, amounts []*big.Int, kefAddr, kifAddr, kpfAddr common.Address, clRes clRegistryResult) (*staking.StakingInfo, error) {
	if len(profiles) == 0 {
		return emptyStakingInfo(num), nil
	}
	if len(profiles) != len(amounts) {
		logger.Error("length of profiles and amounts differ", "sourceNum", num, "profileLen", len(profiles), "amountLen", len(amounts))
		return nil, staking.ErrAddressBookResult
	}
	if len(clRes.NodeIds) != len(clRes.ClPools) || len(clRes.NodeIds) != len(clRes.StakingAmounts) {
		logger.Error("length of CL registry result fields differ", "sourceNum", num, "nodeLen", len(clRes.NodeIds), "poolLen", len(clRes.ClPools), "amountLen", len(clRes.StakingAmounts))
		return nil, staking.ErrCLRegistryResult
	}

	nodeIds := make([]common.Address, 0, len(profiles))
	stakingContracts := make([]common.Address, 0, len(profiles))
	rewardAddrs := make([]common.Address, 0, len(profiles))
	stakingAmounts := make([]uint64, 0, len(profiles))
	for i, p := range profiles {
		if !valset.NodeState(p.State).IsRewardEligible() {
			continue
		}
		nodeIds = append(nodeIds, p.NodeId)
		stakingContracts = append(stakingContracts, p.StakingContract)
		rewardAddrs = append(rewardAddrs, p.RewardAddress)
		stakingAmounts = append(stakingAmounts, new(big.Int).Div(amounts[i], big.NewInt(params.KAIA)).Uint64())
	}

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

	return &staking.StakingInfo{
		SourceBlockNum:   num,
		NodeIds:          nodeIds,
		StakingContracts: stakingContracts,
		RewardAddrs:      rewardAddrs,
		KEFAddr:          kefAddr,
		KIFAddr:          kifAddr,
		KPFAddr:          kpfAddr,
		StakingAmounts:   stakingAmounts,
		CLStakingInfos:   clStakingInfos,
	}, nil
}
```

**File:** kaiax/staking/impl/getter.go (L286-288)
```go
	for i, a := range amounts {
		stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()
	}
```
