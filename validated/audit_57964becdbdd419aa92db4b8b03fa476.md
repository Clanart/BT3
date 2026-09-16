### Title
Reward distribution sends KAIA to `address(0)` with no validation when `CLStakingInfo.CLPoolAddr` is unset, permanently burning validator rewards - (File: kaiax/staking/staking_info.go, kaiax/reward/impl/getter.go)

### Summary
The reward module distributes a validator's consensus-liquidity (CL) staking reward portion directly to `cn.CLStakingInfo.CLPoolAddr` without ever checking that this address is non-zero, unlike every other treasury/fund address (`KIFAddr`, `KEFAddr`, `KPFAddr`) which is explicitly checked with `common.EmptyAddress(...)` and redirected to the proposer if unset.

### Finding Description
`consolidatedNode.Split()` splits a reward amount between the CN staking address and the CL pool address whenever `CLStakingInfo` is non-nil: [1](#0-0) 

This CL amount is then credited directly to `cn.CLStakingInfo.CLPoolAddr` in multiple reward-allocation code paths, with no zero-address guard: [2](#0-1) [3](#0-2) [4](#0-3) 

Compare this to how `KIFAddr`/`KEFAddr`/`KPFAddr` are handled just a few lines away in the same functions — each is checked with `common.EmptyAddress(...)` before use, and redirected to the proposer if empty: [5](#0-4) 

No equivalent check exists for `CLPoolAddr`. The `CLStakingInfo` struct itself carries `CLPoolAddr` as populated from the external `CLRegistry` contract via the staking module's parsing logic, again with no zero-address validation: [6](#0-5) [7](#0-6) 

Finally, `FinalizeState` credits every recipient address in the resulting `RewardSpec.Rewards` map (including a potential `address(0)` entry) unconditionally via `state.AddBalance`: [8](#0-7) 

If `CLRegistry` (an external, in-scope-adjacent system contract not itself present in this repo) ever reports a `CLPoolAddr` of `address(0)` for a validator with a non-zero `CLStakingAmount` — whether due to a registry bug, a race during CL-pool deregistration, or a misconfiguration — the CL portion of that validator's minting/staking reward is credited to `address(0)` every block from that point on, with the tokens permanently unrecoverable (KAIA sent to `address(0)` cannot be reclaimed by anyone, including governance).

### Impact Explanation
This is a direct analog of the reported Merit Circle bug class: a configuration edge-case that the contract fails to reject/redirect results in continuous, irrecoverable loss of funds. Here the funds are native KAIA block rewards computed and distributed deterministically every block by consensus code (`FinalizeState`/`getDeferredRewardFullKore`/Flex variants), so the leak recurs every block for as long as the misconfiguration persists, rather than being a one-time event. This constitutes concrete unauthorized value destruction from the reward pool that should have gone to the CL stakers, with no admin or governance path to recover it once burned.

### Likelihood Explanation
Likelihood is Medium: it requires `CLRegistry` (outside this repo, its state is fully external/system-managed) to return a zero `CLPoolAddr` paired with a non-zero staking amount for a validator's `CLNodeId`. This is not directly triggerable by an arbitrary unprivileged transaction sender, but it is squarely in the "staking and reward distribution" / "system contracts" surface explicitly in-scope for this exercise, and the asymmetric handling (KIF/KEF/KPF are defensively checked, CLPoolAddr is not) demonstrates the omission is a genuine gap in the production reward-distribution code rather than an intentional design choice.

### Recommendation
Add the same `common.EmptyAddress(...)` guard used for `KIFAddr`/`KEFAddr`/`KPFAddr` before crediting `cn.CLStakingInfo.CLPoolAddr` in `assignStakingRewards`, `assignStakingRewardsFlex`, `specWithProposerAndFunds`, and `specWithProposerAndFundsFlex` (kaiax/reward/impl/getter.go). If `CLPoolAddr` is empty, redirect the CL portion to the CN's `RewardAddr` (or to the proposer) instead of crediting `address(0)`, mirroring the existing fallback behavior for treasury funds. Additionally, consider validating `CLPoolAddr != address(0)` when parsing `CLRegistry` results in `kaiax/staking/impl/getter.go`, dropping or logging any malformed entries rather than propagating a zero address into `StakingInfo`.

### Proof of Concept
1. Assume `CLRegistry.getAllCLs()` returns an entry `{CLNodeId: N, CLPoolAddr: address(0), CLStakingAmount: X}` for a validator whose `RewardAddr` maps to `N` (via a registry bug, misconfiguration, or transient state during CL pool removal).
2. `parseCallResult`/`parsePermissionlessCallResult` in `kaiax/staking/impl/getter.go` accepts this without validation and stores it in `StakingInfo.CLStakingInfos`.
3. `consolidateNodes()` attaches this `CLStakingInfo` to the corresponding `consolidatedNode` (kaiax/staking/staking_info.go:150-160).
4. During block finalization, `assignStakingRewards`/`assignStakingRewardsFlex` compute a reward for this CN, call `cn.Split(reward)`, and set `alloc[cn.CLStakingInfo.CLPoolAddr] = clAmount` — i.e., `alloc[address(0)] = clAmount` (kaiax/reward/impl/getter.go:514-533, 460-483).
5. `FinalizeState` calls `state.AddBalance(address(0), clAmount)` every block (kaiax/reward/impl/blockstate.go:53-55), permanently burning that reward with no recovery mechanism.

### Citations

**File:** kaiax/staking/staking_info.go (L59-64)
```go
// CLStakingInfo is the staking info from the consensus liquidity since Prague HF.
type CLStakingInfo struct {
	CLNodeId        common.Address `json:"clNodeId"`
	CLPoolAddr      common.Address `json:"clPoolAddr"`
	CLStakingAmount uint64         `json:"clStakingAmount"`
}
```

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

**File:** kaiax/reward/impl/getter.go (L541-556)
```go
	// If KIF, KEF, or KPF address is not set, proposer takes it.
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

**File:** kaiax/staking/impl/getter.go (L290-302)
```go
	// Collect the CL registry results to StakingInfo fields.
	// If there is no CL registry result, it will be nil.
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
