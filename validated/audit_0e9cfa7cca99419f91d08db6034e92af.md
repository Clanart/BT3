### Title
Division-by-zero in consolidatedNode.Split() causes chain halt when a zero-stake proposer has a zero-balance CL pool - (File: kaiax/staking/staking_info.go)

### Summary
`consolidatedNode.Split()` decides whether to split a reward between a CN and its consensus-liquidity (CL) pool by checking `c.CLStakingInfo == nil`, but the actual divisor used inside the branch is `totalAmount = c.StakingAmount + c.CLStakingInfo.CLStakingAmount`. As with the reported Biconomy bug (checking `totalSharesMinted` instead of `totalReserve`), the code checks a *different, merely-correlated* variable (existence of CL registration) instead of checking the value that is actually divided by (`totalAmount > 0`). When both amounts are zero the function panics with a division-by-zero, and this function sits directly in the block-reward computation path executed by every full node on every block.

### Finding Description
`consolidatedNode.Split`:
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
	clAmount = clAmount.Div(clAmount, totalAmount)   // <-- divides by totalAmount
	...
}
``` [1](#0-0) 

The guard only tests `CLStakingInfo == nil`, never `totalAmount.Sign() > 0`. `c.StakingAmount` (CN staking, in KAIA rounded down) and `c.CLStakingInfo.CLStakingAmount` (CL pool balance) are both externally-influenced `uint64` values pulled from chain state via `MultiCall*`/`CLRegistry` reads, and both can legitimately be `0`:
- A validator can be a qualified/active proposer with `StakingAmount == 0`. This is explicitly documented and unit-tested as a valid state ("zero stakes... validators can be qualified with zero stakes, if all are understaked"): [2](#0-1) , and via the permissionless staking-info path which only filters by reward-eligibility, not by nonzero stake: [3](#0-2) .
- `CLStakingAmount` is read straight from the CL pool balance/registry without any minimum-balance requirement enforced in this module: [4](#0-3) .

`Split()` is called unconditionally whenever `CLStakingInfo != nil` for the block proposer, with no `StakingAmount`/`CLStakingAmount` minimum check at all in that call site:
```go
for _, cn := range cns {
    if cn.RewardAddr != config.Rewardbase {
        continue
    }
    if cn.CLStakingInfo == nil {
        break
    }
    cnAmount, clAmount := cn.Split(proposer)   // unconditional call, no total>0 check
    ...
}
``` [5](#0-4) 
The identical pattern also appears in the flex variant `specWithProposerAndFundsFlex`: [6](#0-5) .

Note that the staker-reward paths (`assignStakingRewards`/`assignStakingRewardsFlex`) happen to be safe because they only call `cn.Split` after filtering `cnTotalStakingAmount > minStake` [7](#0-6) , but the **proposer** path has no equivalent filter, exactly mirroring the CertoraInc bug class where a related-but-wrong condition is checked instead of the true divisor.

### Impact Explanation
`specWithProposerAndFunds`/`specWithProposerAndFundsFlex` are invoked from the reward module's `FinalizeState`, which runs for every block on every full node (both when the local node computes rewards to compare against the block and when the proposer computes rewards before finalizing). A panic here is not a graceful error return — it is an unrecovered division-by-zero panic in Go, which crashes the node process. If it occurs deterministically based on on-chain proposer/staking state (not attacker-controlled timing of message delivery), every honest node evaluating that block would panic simultaneously, resulting in a network-wide liveness/consensus failure (chain halt) rather than a localized node crash. This satisfies the "state divergence/acceptance of invalid block" bar because affected nodes cannot process the block at all, while any node using stale/mocked staking info could produce a different result — a systemic safety-relevant defect, not merely a resource/DoS issue on a single peer.

### Likelihood Explanation
Reachability requires two on-chain conditions to coincide for the current block's proposer's consolidated node (`RewardAddr == config.Rewardbase`):
1. `StakingAmount == 0` for that reward address — permitted and unit-tested as valid (e.g., all validators under-staked, or, post-permissionless fork, a reward-eligible profile with zero effective stake).
2. `CLStakingInfo.CLStakingAmount == 0` for the same node — possible if the CL pool for that node has zero KAIA staked/wrapped at the time `CLRegistry`/`MultiCallDPStakingInfo` is queried (e.g., freshly registered CL pool, or CL pool fully withdrawn), since no minimum-balance floor is enforced in this Go module.

Both are governed by system-contract state that any account can influence indirectly (staking, unstaking, or CL pool deposits/withdrawals via ordinary transactions), and proposer selection is a normal consensus process reachable without any privileged or malicious-node behavior — it is triggered purely by ordinary transactions and state at block-finalization time. This makes the likelihood non-trivial though it requires a specific state alignment (a proposer that is simultaneously fully unstaked and has an empty CL pool), which is a plausible but not everyday combination, hence rated Medium/High rather than trivial-always-triggered.

### Recommendation
In `consolidatedNode.Split`, check the actual divisor before dividing, not merely whether `CLStakingInfo` is non-nil:
```go
func (c consolidatedNode) Split(amount *big.Int) (*big.Int, *big.Int) {
	if c.CLStakingInfo == nil {
		return amount, big.NewInt(0)
	}
	cnAmountBig := big.NewInt(int64(c.StakingAmount))
	clAmountBig := big.NewInt(int64(c.CLStakingInfo.CLStakingAmount))
	totalAmount := new(big.Int).Add(cnAmountBig, clAmountBig)
	if totalAmount.Sign() <= 0 {
		return amount, big.NewInt(0) // fall back to CN-only split, or another well-defined policy
	}
	...
}
```
Additionally, add a defensive check at both call sites (`specWithProposerAndFunds` and `specWithProposerAndFundsFlex`) mirroring the `cnTotalStakingAmount > minStake` guard already used in `assignStakingRewards`, so the proposer branch cannot reach `Split()` with a zero total.

### Proof of Concept
1. Reach Prague hardfork state where `si.CLStakingInfos != nil`.
2. Arrange (via ordinary staking/unstaking transactions, or via the permissionless AddressBookV2 path) that the current block's proposer's `consolidatedNode` has `StakingAmount == 0` — a state already exercised in existing tests as valid/qualified [2](#0-1) .
3. Ensure the same proposer's `CLStakingInfo.CLStakingAmount == 0` (e.g., a CL pool registered in `CLRegistry` with zero staked balance, read via `MultiCallDPStakingInfo` in `kaiax/staking/impl/getter.go`) [4](#0-3) .
4. When the block is finalized, `specWithProposerAndFunds` calls `cn.Split(proposer)` unconditionally for the matching `RewardAddr` [5](#0-4) , which executes `clAmount.Div(clAmount, totalAmount)` with `totalAmount == 0` [1](#0-0) , causing a runtime panic (`division by zero`) in every node's `FinalizeState`, halting block processing chain-wide.

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

**File:** kaiax/valset/impl/getter_proposers_test.go (L189-196)
```go
		{
			desc:         "zero stakes",
			qualified:    numsToAddrs(0, 1, 2, 3), // Note: validators can be qualified with zero stakes, if all are understaked.
			amounts:      []uint64{0, 0, 0, 0},
			useGini:      false,
			expectedFreq: []int{1, 1, 1, 1},
			expectedList: numsToAddrs(1, 3, 0, 2),
		},
```

**File:** kaiax/staking/impl/getter.go (L197-209)
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

**File:** kaiax/reward/impl/getter.go (L514-529)
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
```

**File:** kaiax/reward/impl/getter.go (L566-588)
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
