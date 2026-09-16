### Title
Validator/CL reward addresses returned as `address(0)` are credited literally instead of being redirected, permanently locking staking rewards - (File: kaiax/reward/impl/getter.go)

### Summary
Kaia's `kaiax/reward` module distributes minting and fee rewards to a map of recipient addresses derived from `AddressBook`/`CLRegistry` data supplied by the `kaiax/staking` module. While the code explicitly guards against the treasury fund addresses (KIF/KEF/KPF) being the zero address by redirecting their share to the proposer, it does **not** apply the same guard to individual validator reward addresses (`cn.RewardAddr`) or consensus-liquidity pool addresses (`cn.CLStakingInfo.CLPoolAddr`). If either address is configured as `address(0)`, the corresponding reward portion is credited to `common.Address{}` and effectively becomes unspendable/locked forever.

### Finding Description
The reward spec building functions `specWithProposerAndFunds` / `specWithProposerAndFundsFlex` explicitly treat an empty KIF/KEF/KPF address as "no recipient" and roll that share back into the proposer's reward: [1](#0-0) 

This is the exact mitigation pattern recommended in the referenced report (treat `address(0)` as "no recipient" so other fallback logic takes over).

However, `assignStakingRewards` and `assignStakingRewardsFlex`, which distribute the per-validator staking reward share proportional to `cn.RewardAddr`'s excess stake, contain **no equivalent zero-address check**. They allocate directly into `alloc[cn.RewardAddr]` (and, for Prague CL splits, `alloc[cn.CLStakingInfo.CLPoolAddr]`): [2](#0-1) [3](#0-2) 

These allocations flow into `spec.IncRecipient(addr, amount)` and ultimately into `state.AddBalance(addr, amount)` during `FinalizeState`, with no address(0) filtering at any point: [4](#0-3) 

`RewardSpec.Validate()` only rejects negative amounts; it performs no check that recipients are non-zero addresses: [5](#0-4) 

The `RewardAddr` (and `CLPoolAddr`) values originate directly from on-chain `AddressBook`/`CLRegistry` reads with no zero-address filtering in the staking module either: [6](#0-5) 

`StakingInfo.RewardAddrs` is documented as coming straight from the AddressBook triplets without any stated invariant that it must be non-zero: [7](#0-6) 

### Impact Explanation
If a validator's reward address in `AddressBook` (or CL pool address in `CLRegistry`) is ever configured/misconfigured as `address(0)` — whether by operator error, a buggy staking-contract deployment, or a not-yet-activated CN entry — the corresponding share of the block reward (minting reward and/or KIP-82 staking reward, potentially recurring every block while the misconfiguration persists) is credited to the zero address. Because nobody controls the zero address's private key, this value is permanently and irrecoverably locked/burned, unlike the proposer's fee-based fallback which was explicitly protected. This is a concrete leak of protocol/validator value analogous to the referenced "creator fees may be burned" issue, and the amount can accumulate across many blocks before being noticed. This matches the "Medium" classification of the referenced finding: no complete loss of node operation, but continuous leakage of value that should otherwise reach the staker.

### Likelihood Explanation
This requires the AddressBook/CLRegistry contract data to actually contain a zero reward or CL pool address for a registered, reward-eligible CN — something the reward/staking modules trust from an external (governance/AddressBook admin) source rather than validating themselves. This is not attacker-controlled by an ordinary unprivileged transaction sender, but it is a state that can arise from operational misconfiguration or from bugs/edge cases in AddressBook registration flows (e.g., partially registered CN, or a CN deregistering its reward address without deregistering itself), and would not be caught by the module's own reward-distribution logic since no defensive check exists—compare to the deliberate defensive check already present for KIF/KEF/KPF.

### Recommendation
Apply the same address(0) fallback pattern used for KIF/KEF/KPF to per-validator and CL reward addresses in `assignStakingRewards`/`assignStakingRewardsFlex` (and in `specWithProposerAndFunds*`'s CL-split logic): if `cn.RewardAddr` or `cn.CLStakingInfo.CLPoolAddr` is `common.Address{}`, redirect that portion to the proposer (or exclude the CN from `ConsolidatedNodes()`/staking-reward eligibility) rather than crediting the zero address. Additionally, consider adding a check in `RewardSpec.Validate()` that rejects (or the module that builds it) any non-zero-amount reward destined for `common.Address{}`, to catch this class of bug defensively regardless of source.

### Proof of Concept
1. Assume `AddressBook` returns a `StakingInfo` where one qualifying CN has `RewardAddr == common.Address{}` (zero address) but a staking amount exceeding `minStake`/`threshold` (e.g. due to CN operator clearing its reward address, or a bug/edge case in the AddressBook registration/migration flow, while remaining reward-eligible per KIP-286).
2. On each block, `getDeferredRewardFullKore`/`getDeferredRewardFullFlex` calls `assignStakingRewards`/`assignStakingRewardsFlex`, which computes `reward := excess * stakersReward / totalExcess > 0` for this CN and executes `alloc[cn.RewardAddr] = reward` — i.e. `alloc[common.Address{}] = reward`. [8](#0-7) 
3. This gets merged into `spec.Rewards[common.Address{}]` via `spec.IncRecipient`.
4. `FinalizeState` executes `state.AddBalance(common.Address{}, amount)` for every block, permanently crediting the zero address with the misconfigured validator's staking reward share. [9](#0-8) 
5. No validation anywhere in the pipeline (`Validate()`, `IncRecipient`, `FinalizeState`) prevents or reroutes this, so the reward is effectively burned every block until the AddressBook is corrected.

### Citations

**File:** kaiax/reward/impl/getter.go (L460-482)
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
```

**File:** kaiax/reward/impl/getter.go (L514-532)
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
```

**File:** kaiax/reward/impl/getter.go (L596-615)
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

```

**File:** kaiax/reward/impl/blockstate.go (L46-56)
```go
	spec, err := r.GetDeferredReward(header, txs, receipts)
	if err != nil {
		return err
	}
	if err := spec.Validate(); err != nil {
		return err
	}
	for addr, amount := range spec.Rewards {
		state.AddBalance(addr, amount)
	}
	return nil
```

**File:** kaiax/reward/spec.go (L118-125)
```go
func (spec *RewardSpec) Validate() error {
	for addr, amount := range spec.Rewards {
		if amount.Sign() < 0 {
			return errNegativeRewardAmount(addr, amount)
		}
	}
	return nil
}
```

**File:** kaiax/staking/impl/getter.go (L197-233)
```go
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
```

**File:** kaiax/staking/staking_info.go (L37-40)
```go
	// The AddressBook triplets
	NodeIds          []common.Address `json:"councilNodeAddrs"`
	StakingContracts []common.Address `json:"councilStakingAddrs"`
	RewardAddrs      []common.Address `json:"councilRewardAddrs"`
```
