### Title
Division-by-zero panic in `consolidatedNode.Split` during proposer/fund reward splitting can halt block finalization - (File: kaiax/staking/staking_info.go)

### Summary
This is analogous to the reported `StableModule.stableCollateralPerShare` bug class: a value that is assumed to always be positive is used unchecked as a divisor, and under a specific but reachable state it can become zero, causing a revert (in Solidity) / panic (in Go). In Kaia's reward-distribution code, `consolidatedNode.Split` divides by `totalAmount = StakingAmount + CLStakingAmount` without checking it is nonzero, and this function is invoked for the block proposer's reward split without any minimum-stake guard.

### Finding Description
`consolidatedNode.Split` computes the CN/CL split of a reward amount: [1](#0-0) 

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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <-- unguarded division
	...
}
```
If `totalAmount` is `0`, `big.Int.Div` panics ("division by zero"), analogous to the Solidity revert in `stableDepositQuote`.

The staking-reward distribution paths (`assignStakingRewards`, `assignStakingRewardsFlex`) guard this by only calling `Split` when `cnTotalStakingAmount > minStake` (so the combined amount is provably > 0): [2](#0-1) 

However, the **proposer/fund reward split** paths call `Split` on the block proposer's `consolidatedNode` with only a `CLStakingInfo != nil` check — no check that `StakingAmount` or `CLStakingAmount` is nonzero: [3](#0-2) [4](#0-3) 

```go
cns := si.ConsolidatedNodes()
for _, cn := range cns {
    if cn.RewardAddr != config.Rewardbase {
        continue
    }
    if cn.CLStakingInfo == nil {
        break
    }
    cnAmount, clAmount := cn.Split(proposer)   // no zero-check on StakingAmount/CLStakingAmount
    ...
}
```

For `totalAmount` to be zero, the proposer's own CN `StakingAmount` and its registered CL pool's `CLStakingAmount` must both be `0` at the time `StakingInfo` was snapshotted. Normally a validator must satisfy `reward.minstake` to be a qualified proposer, which would keep `StakingAmount > 0`. But `reward.minstake` is a governance parameter (`MinimumStake`), and, like `KIP71.BaseFeeDenominator` in this same codebase, several such governance-tunable numeric parameters are only checked for type, not for being strictly positive (see the analogous zero-tolerant handling of `BaseFeeDenominator` at [5](#0-4) , which explicitly special-cases the zero value — showing governance can legitimately set threshold-like parameters to values that break arithmetic assumptions elsewhere). If `reward.minstake` is configured to `0`, a validator with zero CN staking can become qualified and be elected proposer; if that validator also has a CL pool entry with `CLStakingAmount == 0` (e.g., a freshly registered but empty CL pool, or one from which all consensus liquidity has since been withdrawn) recorded in `CLStakingInfo`, then `Split` divides by zero.

### Impact Explanation
A panic inside `specWithProposerAndFunds`/`specWithProposerAndFundsFlex`, which are called from `FinalizeState` during deferred-reward distribution, would occur deterministically on every full node processing that block, causing a chain-wide halt rather than a state divergence — a severe availability impact for block production/finalization. This matches the impact class in the reference report ("unable to proceed because of divide-by-zero"), scaled up to consensus-critical code.

### Likelihood Explanation
Reaching this path requires: (1) the `reward.minstake` governance parameter to be set to `0` (a privileged governance action, not a public unprivileged action, which lowers likelihood significantly), and (2) a validator with zero own CN stake but with an associated CL pool of zero staking amount to become the elected proposer at some point after Prague/flex-reward activation. Because condition (1) requires a governance decision, this cannot be triggered by an ordinary unprivileged transaction sender alone, which weakens the strength of this analog relative to the original report (where the short side could trigger the bug purely through market price movement). I could not fully verify within the available context whether `MinimumStake`'s `FormatChecker` rejects zero (the checker code lines were not returned by the search), so this is a Medium-confidence, conditionally-reachable finding rather than a fully proven one.

### Recommendation
Add an explicit guard in `consolidatedNode.Split` to treat a zero `totalAmount` as "all funds go to the CN" (mirroring the safe fallback used elsewhere, e.g., `stableCollateralPerShare`'s `totalSupply == 0` branch), e.g.:
```go
if totalAmount.Sign() == 0 {
    return amount, big.NewInt(0)
}
```
Additionally, confirm/enforce that `reward.minstake` (`MinimumStake` governance parameter) cannot be set to `0`, and that CL pools with zero staking amount are excluded from `CLStakingInfos` before being attached to a `consolidatedNode`.

### Proof of Concept
Not independently executed; based on static code-path analysis:
1. Governance sets `reward.minstake = 0`.
2. A validator node registers with `CNStaking = 0` and is included in the qualified validator set (since `minStake` check becomes `>= 0`), and is subsequently selected as block proposer.
3. That validator has a `CLStakingInfo` entry (e.g., a CL pool with `CLStakingAmount = 0`, achievable if all delegators withdraw from the CL pool) attached via `consolidateNodes()` ( [6](#0-5) ).
4. During `FinalizeState`, `specWithProposerAndFunds`/`specWithProposerAndFundsFlex` calls `cn.Split(proposer)` with `StakingAmount = 0` and `CLStakingInfo.CLStakingAmount = 0`, causing `totalAmount = 0` and a `big.Int` division-by-zero panic, crashing block finalization on all nodes processing that block.

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

**File:** kaiax/reward/impl/getter.go (L514-525)
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

**File:** kaiax/reward/impl/getter.go (L622-637)
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
```

**File:** params/kip71_config.go (L70-76)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
```
