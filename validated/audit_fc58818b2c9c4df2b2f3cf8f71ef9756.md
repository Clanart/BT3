### Title
Division-by-zero panic in `consolidatedNode.Split()` when both CN staking amount and CL staking amount round down to zero - ([File: kaiax/staking/staking_info.go])

### Summary
`consolidatedNode.Split()` divides a reward amount by `totalAmount = StakingAmount + CLStakingInfo.CLStakingAmount` without checking that `totalAmount` is non-zero. Both components are computed elsewhere by truncating integer division of the raw wei/`params.KAIA` amount, which rounds any staked balance under 1 KAIA down to `0`. If the block proposer's consolidated node ends up with `StakingAmount == 0` and an attached `CLStakingInfo.CLStakingAmount == 0`, `Split()` performs `big.Int.Div(x, 0)`, which panics in Go rather than returning an error, crashing the reward-distribution path executed by every full node.

### Finding Description
`consolidatedNode.Split` is defined as: [1](#0-0) 

`StakingAmount` and `CLStakingInfo.CLStakingAmount` are derived by truncating (integer-dividing) the raw on-chain staking balance by `params.KAIA` (1e18 kei): [2](#0-1) [3](#0-2) 

This means any CN staking-contract balance or CL-pool staking balance strictly less than 1 KAIA is recorded as `0` in `StakingInfo`. `Split()` is invoked unconditionally for the block proposer's consolidated node — regardless of any minimum-stake threshold — from both non-flex and flex reward paths: [4](#0-3) [5](#0-4) 

Unlike the staker-allocation loops (`assignStakingRewards`/`assignStakingRewardsFlex`), which only call `cn.Split()` for validators whose consolidated stake exceeds `minStake`/`threshold` (guaranteeing a non-zero denominator), `specWithProposerAndFunds`/`specWithProposerAndFundsFlex` call `cn.Split(proposer)` for whichever consolidated node matches `config.Rewardbase`, with the only guard being that `cn.CLStakingInfo != nil`: [6](#0-5) 

If that proposer's CN has `StakingAmount == 0` (CNStaking balance < 1 KAIA) and its registered CL pool also currently holds `< 1 KAIA` (e.g., a newly registered CL pool with negligible/zero deposits, or one that has been fully withdrawn), `totalAmount = 0 + 0 = 0`, and `clAmount.Div(clAmount, totalAmount)` triggers a runtime panic (division by zero) rather than a handled error.

### Impact Explanation
This is on the deferred-reward finalization path (`FinalizeState`), which every node executing/validating the chain must run for each block once Prague (KIP-226 consensus liquidity) is active. A panic here crashes node processes attempting to finalize/verify that block, producing a chain-wide liveness halt (denial of service across honest full nodes) rather than a contained, per-transaction revert. This matches the "state transition" and "staking and reward distribution" categories explicitly in scope, and the impact (state divergence/halt from an invalid/unhandled edge case in consensus-critical code) is High.

### Likelihood Explanation
Exploitability depends on a validator becoming the block proposer while its consolidated CN staking amount and its associated consensus-liquidity pool staking amount both round down to 0 in KAIA units. This requires the validator to (a) remain reward-eligible/participate in the validator set with a CNStaking balance under 1 KAIA, and (b) have a CL pool registered via CLRegistry with staked balance also under 1 KAIA. Both conditions are plausible: a validator's stake can be temporarily reduced by withdrawal below 1 KAIA while still registered, and a CL pool can be freshly registered/near-empty. Because triggering the crash only requires becoming the proposer once with this configuration (no privileged access beyond normal validator operation), likelihood is Medium, but impact severity (network-wide panic) elevates overall severity.

### Recommendation
Add an explicit zero-check before dividing in `consolidatedNode.Split()`:
```go
func (c consolidatedNode) Split(amount *big.Int) (*big.Int, *big.Int) {
    if c.CLStakingInfo == nil {
        return amount, big.NewInt(0)
    }
    cnAmountBig := big.NewInt(int64(c.StakingAmount))
    clAmountBig := big.NewInt(int64(c.CLStakingInfo.CLStakingAmount))
    totalAmount := new(big.Int).Add(cnAmountBig, clAmountBig)
    if totalAmount.Sign() == 0 {
        // No stake in either pool; assign entire amount to CN (or handle per policy).
        return amount, big.NewInt(0)
    }
    clAmount := new(big.Int).Mul(clAmountBig, amount)
    clAmount = clAmount.Div(clAmount, totalAmount)
    cnAmount := new(big.Int).Sub(amount, clAmount)
    return cnAmount, clAmount
}
```
Additionally, review `specWithProposerAndFunds`/`specWithProposerAndFundsFlex` to skip the `Split()` call (fall back to `IncRecipient(config.Rewardbase, proposer)`) when the matched CN's combined staking amount is zero.

### Proof of Concept
1. A validator `V` operates a CNStaking contract with a balance just under `1 * params.KAIA` (e.g., 0.5 KAIA) — legitimate low-stake operation, or achieved by withdrawing most of its stake after registration.
2. `V` also registers (or is delegated) a Prague-era CL staking pool via `CLRegistry` whose current staked balance is also under 1 KAIA (e.g., freshly registered pool with no/negligible deposits yet).
3. `getFromState`/`parseCallResult` truncates both amounts to `0`: [7](#0-6) 
4. `V` is selected as block proposer for a block after the Prague hardfork (`config.Rules.IsPrague == true`) with `si.CLStakingInfos != nil`.
5. During deferred reward computation, `specWithProposerAndFunds` (or its Flex counterpart) locates `V`'s consolidated node, sees `cn.CLStakingInfo != nil`, and calls `cn.Split(proposer)`: [4](#0-3) 
6. Inside `Split()`, `totalAmount = 0`, and `clAmount.Div(clAmount, totalAmount)` panics with "division by zero", crashing every node executing `FinalizeState`/`GetDeferredReward` for that block.

Note: full end-to-end confirmation would require tracing exact validator eligibility rules in `kaiax/valset` (whether a CN with truncated `StakingAmount == 0` can still be an active/reward-eligible proposer) and the CLRegistry minimum-deposit enforcement, which the indexed code did not fully resolve within the available search budget — these should be verified directly in a Devin session with full repository access.

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

**File:** kaiax/reward/impl/getter.go (L622-638)
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
