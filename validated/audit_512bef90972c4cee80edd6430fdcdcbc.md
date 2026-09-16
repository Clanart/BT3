### Title
Division-by-zero panic in `consolidatedNode.Split` when a CN's staking amount and its consensus-liquidity (CL) staking amount both truncate to zero KAIA - ([File: kaiax/staking/staking_info.go])

### Summary
`consolidatedNode.Split` divides a reward amount by `totalAmount = StakingAmount + CLStakingInfo.CLStakingAmount` without checking that `totalAmount != 0`. Both `StakingAmount` and `CLStakingAmount` are derived by truncating raw wei balances down to whole-KAIA units, so a validator (staker) that keeps its CN staking-contract balance and its registered CL-pool balance each below `1 KAIA` produces `StakingAmount == 0` and `CLStakingAmount == 0` while `CLStakingInfo != nil`. This function is called unconditionally for the block proposer's reward split in `specWithProposerAndFunds`/`specWithProposerAndFundsFlex`, with no `minStake`/nonzero guard, causing a `big.Int` division-by-zero panic during block reward finalization (state transition), matching the same "unvalidated zero denominator" bug class as the reported `getProtocolFee` issue.

### Finding Description
`Split` is defined as:
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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <-- panics if totalAmount == 0
	...
}
``` [1](#0-0) 

`StakingAmount` and `CLStakingAmount` are populated by truncating wei to whole KAIA units (`Div(a, params.KAIA)`), so any balance under `1 KAIA` (10^18 wei) becomes `0`: [2](#0-1) 

`consolidateNodes()` attaches `CLStakingInfo` to a reward address as soon as a matching `CLNodeId` is found in the CL registry, with no minimum-amount filter: [3](#0-2) 

`specWithProposerAndFunds` (used by the Kore reward path) calls `cn.Split(proposer)` for the block's reward-base validator whenever `cn.CLStakingInfo != nil`, with **no check on `cn.StakingAmount` or `minStake`** before the call:
```go
cns := si.ConsolidatedNodes()
for _, cn := range cns {
    if cn.RewardAddr != config.Rewardbase {
        continue
    }
    if cn.CLStakingInfo == nil {
        break
    }
    cnAmount, clAmount := cn.Split(proposer)   // unguarded
    ...
}
``` [4](#0-3) 

The same unguarded pattern exists in `specWithProposerAndFundsFlex`: [5](#0-4) 

This differs from the properly-guarded stake-distribution helpers (`assignStakingRewards`/`assignStakingRewardsFlex`), which only call `Split` after confirming `cnTotalStakingAmount > minStake`, guaranteeing a nonzero denominator there: [6](#0-5) 

The proposer-reward path has no equivalent guard, so it is the vulnerable entry point.

### Impact Explanation
`specWithProposerAndFunds`/`specWithProposerAndFundsFlex` execute during deferred-fee reward finalization (`FinalizeState`) on every block after the Prague/KIP-226 hardfork, for the block's proposer/reward address. If the block proposer's `RewardAddr` has: (a) a registered `CLStakingInfo` (i.e., it has enrolled a CL pool via the CL registry), and (b) both its CN staking-contract balance and its CL pool balance are below `1 KAIA` at that source block, `totalAmount` is `0` and `big.Int.Div` panics. This crashes every full node that executes the state transition for that block — a consensus-critical DoS that halts block processing chain-wide, not merely a single node's view. This satisfies the "acceptance of an invalid transaction/block" / "state divergence between honest nodes" criteria, since it is a deterministic panic embedded in the mandatory block-finalization logic.

### Likelihood Explanation
This requires the affected reward address to already be a consolidated, registered CN/staker (already reachable in-scope actor: "staker") that is also selected as block proposer at least once while its combined truncated stake is zero. A staker fully controls its own CN staking-contract withdrawals and its own CL-pool deposit/withdrawal amount, so it can deliberately reduce both balances under 1 KAIA (dust) at a moment when it is due to propose a block, without needing any additional privilege beyond being a registered validator (staking/reward distribution is explicitly listed as an in-scope reachable domain, and "staker" is an explicitly permitted actor). No governance or admin action is required to trigger the panic — only manipulation of the staker's own on-chain balances.

### Recommendation
In `consolidatedNode.Split`, and in the proposer-reward call sites (`specWithProposerAndFunds`, `specWithProposerAndFundsFlex`) that invoke it, check `totalAmount.Sign() == 0` (or equivalently `StakingAmount == 0 && CLStakingInfo.CLStakingAmount == 0`) before dividing, and fall back to allocating the entire amount to the CN (as done in the `CLStakingInfo == nil` branch) instead of performing the division. Alternatively, gate the `Split` call behind the same `minStake`/nonzero-stake guard already used in `assignStakingRewards`/`assignStakingRewardsFlex`.

### Proof of Concept
1. A CN operator registers a CL pool for its node via the CL registry contract, obtaining a non-nil `CLStakingInfo` entry for its `RewardAddr` (per `consolidateNodes`). [3](#0-2) 
2. The operator withdraws its CN staking-contract balance to below `1 KAIA` and ensures the CL pool balance is also below `1 KAIA`, so both `StakingAmounts[i]` and `CLStakingAmount` truncate to `0` in `parseCallResult`. [2](#0-1) 
3. When a block is produced whose `header.Rewardbase` equals this CN's `RewardAddr` (i.e., it acts as proposer), `getDeferredRewardFullKore`/`getDeferredRewardFullFlex` calls `specWithProposerAndFunds`/`specWithProposerAndFundsFlex`, which finds `cn.RewardAddr == config.Rewardbase` and `cn.CLStakingInfo != nil`, and calls `cn.Split(proposer)`. [4](#0-3) 
4. Inside `Split`, `totalAmount = 0 + 0 = 0`, and `clAmount.Div(clAmount, totalAmount)` panics with a division-by-zero error, crashing the node executing `FinalizeState` for that block. [1](#0-0)

### Citations

**File:** kaiax/staking/staking_info.go (L150-160)
```go
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

**File:** kaiax/staking/impl/getter.go (L286-301)
```go
	for i, a := range amounts {
		stakingAmounts[i] = big.NewInt(0).Div(a, big.NewInt(params.KAIA)).Uint64()
	}

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

**File:** kaiax/reward/impl/getter.go (L617-638)
```go
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
