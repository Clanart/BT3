## Title
Division by zero in `consolidatedNode.Split()` can crash consensus-liquidity reward distribution when a validator's combined CN + CL stake is zero - (File: `kaiax/staking/staking_info.go`)

## Summary
This is a plausible analog of the reported bug class (ratio-based amount split that divides by an attacker/state-influenceable denominator that can reach zero). In the reported Solidity issue, `calculateMaxDeposit()` divides by `ratio0`/`ratio1`, which can become `0` at boundary prices, causing a revert. In Kaia's reward-distribution code, `consolidatedNode.Split()` performs an analogous ratio split by dividing by `totalAmount = StakingAmount + CLStakingAmount`, which is not proven to always be non-zero on every call path.

## Finding Description
`consolidatedNode.Split()` computes a proportional split of a reward amount between a Consensus Node (CN) and its associated Consensus Liquidity (CL) pool: [1](#0-0) 

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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <-- division by totalAmount
	...
```

`totalAmount` is guaranteed non-zero at the call sites inside `assignStakingRewards`/`assignStakingRewardsFlex`, because those call sites only invoke `Split()` after confirming `cnTotalStakingAmount > minStake` (which implies `StakingAmount + CLStakingAmount > 0`): [2](#0-1) 

However, the proposer-reward call sites `specWithProposerAndFunds` and `specWithProposerAndFundsFlex` invoke `cn.Split(proposer)` guarded **only** by `cn.CLStakingInfo != nil` — they do **not** check that `cn.StakingAmount` or `CLStakingAmount` is non-zero: [3](#0-2) [4](#0-3) 

If a node has a registered `CLStakingInfo` entry (i.e. `CLNodeId` is found in the AddressBook mapping via `consolidateNodes()`) but both `StakingAmount == 0` and `CLStakingInfo.CLStakingAmount == 0`, `totalAmount` becomes `big.NewInt(0)`, and `big.Int.Div` **panics** (Go's `big.Int` division by zero raises a runtime panic, not a recoverable error), rather than returning an error like Solidity's `revert`.

## Impact Explanation
If this code path is reachable for the block proposer (`config.Rewardbase == cn.RewardAddr`), a panic during `FinalizeState()`/reward calculation would crash node processing for that block — a state-transition failure reachable by simply having a validator/proposer registered in the CL registry with zero CN and zero CL stake at reward-calc time. This is a state-transition correctness/availability issue (potential state divergence between honest nodes if only some nodes hit this state, or a chain-halting panic if it hits universally), which maps to "Medium" impact under the same "amount ratio can reach zero → division by zero → transaction/block-processing failure" bug class as the original report.

## Likelihood Explanation
Likelihood is Medium-Low and **not fully confirmed**: I could not, within the tool budget, determine (a) whether the underlying data source (`kaiax/staking/impl/getter.go`'s `parsePermissionlessCallResult` / CLRegistry contract) can ever produce a `CLStakingInfo` entry for a node whose `StakingAmount` is simultaneously 0 (e.g. after a node unstakes below `minStake` but still appears in AddressBook with zero effective stake, per KIP-286/287 filtering), nor (b) whether governance can set `MinimumStake` to 0 making the earlier `StakingAmount < minStake` eligibility check trivially satisfied even at StakingAmount=0. Confirming reachability requires deeper analysis of the AddressBook/CLRegistry contracts and the `emptyStakingInfo`/filtering logic in `kaiax/staking/impl/getter.go`, which I was unable to fully trace due to iteration limits.

## Recommendation
In `consolidatedNode.Split()`, add an explicit guard for `totalAmount.Sign() == 0` and short-circuit to returning `(amount, big.NewInt(0))` (equivalent to no CL) before performing the division, mirroring how the original report recommends handling zero-ratio cases in `calculateMaxDeposit()`. Additionally, audit all callers (`specWithProposerAndFunds`, `specWithProposerAndFundsFlex`, `assignStakingRewards`, `assignStakingRewardsFlex`) to ensure they only invoke `Split()` when `StakingAmount + CLStakingAmount > 0`.

## Proof of Concept
Not independently verified end-to-end due to inability to confirm from CLRegistry/AddressBook contract logic whether a `consolidatedNode` with `CLStakingInfo != nil` and `StakingAmount == 0 && CLStakingAmount == 0` can occur for the block's proposer. Conceptually:
1. Construct/mock a `StakingInfo` where `RewardAddrs` includes the proposer's reward address with `StakingAmounts[i] = 0`, and a `CLStakingInfos` entry mapping to the same node with `CLStakingAmount = 0`.
2. Call `getDeferredRewardFullKore`/`getDeferredRewardFullFlex` (Prague rules enabled) with `config.Rewardbase` equal to that proposer's reward address.
3. `specWithProposerAndFunds`/`specWithProposerAndFundsFlex` calls `cn.Split(proposer)`, which panics inside `big.Int.Div` due to `totalAmount == 0`. [5](#0-4)

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

**File:** kaiax/reward/impl/getter_test.go (L618-632)
```go
func TestGetDeferredRewardFull(t *testing.T) {
	var (
		mintingAmount  = big.NewInt(6.4e18) // 6.40 KAIA
		rewardRatio, _ = reward.NewRewardRatio("50/20/30")
		kip82Ratio, _  = reward.NewRewardKip82Ratio("20/80")
		config         = &reward.RewardConfig{
			Rewardbase:    common.HexToAddress("0xfff"),
			MintingAmount: mintingAmount,
			MinimumStake:  big.NewInt(5_000_000),
			RewardRatio:   rewardRatio,
			Kip82Ratio:    kip82Ratio,
		}
		lowFee  = big.NewInt(7e16) // 0.07 KAIA
		highFee = big.NewInt(2e18) // 2.00 KAIA (F/2 > gpM)
	)
```
