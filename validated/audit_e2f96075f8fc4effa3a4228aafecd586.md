### Title
Unbounded per-block iteration over AddressBook/CLRegistry-derived staking lists causes reward-distribution and consensus-critical validator computations to grow without bound - (File: kaiax/staking/impl/getter.go, kaiax/staking/staking_info.go, kaiax/reward/impl/getter.go)

### Summary
The reported vetoken bug is a classic "unbounded array growth appended by a privileged `addReward()`, then iterated every time a gas-critical function runs" DoS. In Kaia, the structurally identical pattern exists in the staking/valset/reward pipeline: `StakingInfo.NodeIds/StakingContracts/RewardAddrs/CLStakingInfos` are built every block from the AddressBook/AddressBookV2 and CLRegistry system contracts (`kaiax/staking/impl/getter.go:parseCallResult`/`parsePermissionlessCallResult`), and these slices are iterated in full, every single block, by `StakingInfo.consolidateNodes()` (`kaiax/staking/staking_info.go:124-167`), by `assignStakingRewards`/`assignStakingRewardsFlex` (`kaiax/reward/impl/getter.go:421-534`) inside `FinalizeState` (`kaiax/reward/impl/blockstate.go:30-57`), and by valset demotion/qualification logic (`kaiax/valset/impl/getter_demote.go`). Unlike the vetoken `addReward()`, which was owner-gated, growth of the underlying lists here is driven by CN/CL registrations that, post-Permissionless hardfork (KIP-290) and for CLRegistry, are intentionally not limited to a small governance-controlled set — new entries can keep being added over the life of the chain. If this list is allowed to grow large enough, the O(n) loops that run unconditionally on **every block's** `FinalizeState`/reward-distribution/valset-computation path become expensive enough to threaten block-processing performance chain-wide, rather than merely DoSing a single claim transaction as in the original report.

### Finding Description
- `kaiax/staking/impl/getter.go:getFromState` builds a full `StakingInfo` from AddressBook (permissioned) or AddressBookV2 (permissionless) plus CLRegistry data on every block via `MultiCallStakingInfo`/`MultiCallStakingInfoPermissionless`/`MultiCallDPStakingInfo` (lines 103-180).
- The parsed `NodeIds`, `StakingContracts`, `RewardAddrs`, `StakingAmounts`, and `CLStakingInfos` are unbounded Go slices whose size is determined entirely by on-chain registration state [1](#0-0) .
- Every block, `StakingInfo.ConsolidatedNodes()` fully iterates `NodeIds`/`RewardAddrs`/`CLStakingInfos` to build a consolidated map [2](#0-1) .
- The consolidated list is then iterated again (often twice) inside `assignStakingRewards`/`assignStakingRewardsFlex`, which run unconditionally as part of `FinalizeState` at the end of every block to distribute deferred rewards [3](#0-2) [4](#0-3) .
- The same underlying council/CN list also feeds `getDemotedValidatorsIstanbul`, which iterates `council.List()` on every block to determine qualified/demoted validators used for proposer/committee selection and seal verification [5](#0-4) [6](#0-5) .
- Unlike the vetoken `addReward()` (owner-only, no cap, no removal), growth of this list is not owner-gated at the same layer: (a) AddressBookV2/Permissionless CN registration exists specifically to remove the governance-vote gate that bounded validator/council growth pre-Permissionless (the "AddressBookV2 (KIP-290)... committee is derived from on-chain state" comment in `kaiax/gov/param.go:574-582`), and (b) CLRegistry CL-pool registration (read via `MultiCallDPStakingInfo`) is a staking-based, not owner-gated, registration mechanism whose contract source (`CLRegistry.sol`) is not present in this indexed snapshot, so its cap (if any) could not be directly confirmed from the code available here.
- There is a `MaxNodeCount` parameter wired into `AllocPermissionlessConfig`/`ABv2DataContract` for AddressBookV2 CN registration (seen in `blockchain/system/permissionless_test.go` and `cmd/homi/setup/cmd.go`), which suggests CN growth via ABv2 is explicitly capped by design — this is the "hard limit" mitigation the original report recommended, and it appears to have been already applied for CN registration. However, I could not verify from the indexed code whether the same bound is enforced for the CLRegistry-derived `CLStakingInfos` list (the CLRegistry contract implementation was not found in the index, only its mock/test double `contracts/testing/reward/CLRegistryMock.sol`), so this leaves the CL side unbounded/unverified rather than a confirmed unbounded finding.

### Impact Explanation
Because these loops execute unconditionally inside `FinalizeState` (deferred reward distribution) and inside valset qualification/demotion, growth of the underlying staking/CL list — if not capped — degrades block-processing cost for **every node on the network**, not just a single user's claim transaction as in the original vetoken report. This is more severe than the original: it risks state divergence between honest nodes if execution time diverges near resource limits, or an outright denial of service on reward distribution/block finalization chain-wide, rather than a single contract's fund lock. Given the explicit `MaxNodeCount` cap found for AddressBookV2 CN registration, the concretely exploitable analog (if any) would have to come through the CLRegistry CL-pool registration path, whose registration/cap logic is not present in the indexed codebase and therefore cannot be confirmed as unbounded or bounded with the available evidence.

### Likelihood Explanation
Low-to-uncertain. The CN-registration vector (AddressBookV2/Permissionless) already carries an explicit `MaxNodeCount` bound matching the report's own recommended mitigation, closing off that specific vector. The CL-registration vector's cap could not be verified because `CLRegistry.sol`'s real implementation is outside what the index returned (only a test mock was found), so likelihood for that path is unknown rather than confirmed.

### Recommendation
Confirm that the production `CLRegistry` contract enforces an explicit maximum number of registered CL pools/nodes (mirroring `MaxNodeCount`/`MaxValActivePausedCount` already used for AddressBookV2), and add monitoring/alerting on `len(StakingInfo.CLStakingInfos)` and `len(StakingInfo.NodeIds)` growth so that reward-distribution and valset computation cost per block stays bounded as recommended in the original report (hard cap on list size, or restructure reward/committee computation to avoid O(n) full-list iteration in the hot per-block path).

### Proof of Concept
Not independently constructible from the indexed code: exploiting this analog requires demonstrating that CLRegistry CL-pool registration is (a) permissionless/uncapped and (b) reachable via a single transaction that a normal staker/GC can send, which requires the actual `CLRegistry.sol` source (not present in this index) to confirm access control and any cap. Absent that contract, this cannot be escalated beyond a plausible-but-unverified analog for the CL-registration side; the CN-registration side is confirmed bounded by `MaxNodeCount` and therefore does not qualify as a valid analog.

### Citations

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

**File:** kaiax/staking/staking_info.go (L124-167)
```go
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

**File:** kaiax/reward/impl/blockstate.go (L29-57)
```go
// Distribute the deferred rewards at the end of block processing.
func (r *RewardModule) FinalizeState(header *types.Header, state *state.StateDB, txs []*types.Transaction, receipts []*types.Receipt) error {
	if r.GovModule.GetParamSet(header.Number.Uint64()).ProposerPolicy == uint64(istanbul.WeightedRandom) && common.EmptyHash(header.Root) {
		qualified, err := r.ValsetModule.GetQualifiedValidators(header.Number.Uint64())
		if err != nil {
			return err
		}
		useRewardAddress := valset.NewAddressSet(qualified).Contains(r.NodeAddress)

		if rewardAddr := r.GetRewardAddress(header.Number.Uint64(), r.NodeAddress); useRewardAddress && rewardAddr != (common.Address{}) {
			header.Rewardbase = rewardAddr
			logger.Trace("Use reward address for nodeValidator", "header.Number", header.Number.Uint64(), "nodeAddress", r.NodeAddress, "rewardbase", header.Rewardbase)
		} else {
			logger.Trace("No reward address for nodeValidator. Use node's rewardbase.", "header.Number", header.Number.Uint64(), "nodeAddress", r.NodeAddress, "rewardbase", header.Rewardbase)
		}
	}

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
}
```

**File:** kaiax/valset/impl/getter_demote.go (L78-104)
```go
func getDemotedValidatorsIstanbul(council *valset.AddressSet, si *staking.StakingInfo, pset gov.ParamSet) *valset.AddressSet {
	var (
		demoted        = valset.NewAddressSet(nil)
		singleMode     = pset.GovernanceMode == "single"
		governingNode  = pset.GoverningNode
		minStake       = pset.MinimumStake.Uint64() // in KAIA
		stakingAmounts = collectStakingAmounts(council.List(), si)
	)

	// First filter by staking amounts.
	for _, node := range council.List() {
		if uint64(stakingAmounts[node]) < minStake {
			demoted.Add(node)
		}
	}

	// If all validators are demoted, then no one is demoted.
	if demoted.Len() == len(council.List()) {
		demoted = valset.NewAddressSet(nil)
	}

	// Under single governance mode, governing node cannot be demoted.
	if singleMode && demoted.Contains(governingNode) {
		demoted.Remove(governingNode)
	}
	return demoted
}
```

**File:** blockchain/block_validator.go (L286-318)
```go
	}

	qualified, err := v.mValset.GetQualifiedValidators(blockNum)
	if err != nil {
		return err
	}
	qualifiedSet := valset.NewAddressSet(qualified)
	if !qualifiedSet.Contains(author) {
		return consensus.ErrUnauthorized
	}

	// Reached only pre-permissionless (post-fork returns above); count seals against the council.
	council, err := v.mValset.GetCouncil(blockNum)
	if err != nil {
		return err
	}
	signerSet := valset.NewAddressSet(council).Copy()
	validSeal, err := countValidCommittedSeals(committers, signerSet)
	if err != nil {
		return err
	}

	qualifiedLen := len(qualified)
	committeeSize := qualifiedLen
	if !gov.DeprecatedAt(gov.IstanbulCommitteeSize, rules) {
		committeeSize = int(v.mGov.GetParamSet(blockNum).CommitteeSize)
	}
	// Pre-permissionless uses the legacy 2f+1 quorum. sealer.Quorum now returns
	// ceil(2N/3), so compute 2f+1 explicitly to preserve historical header validity.
	if validSeal < 2*v.sealer.F(blockNum, qualifiedLen, committeeSize)+1 {
		return istanbul.ErrInvalidCommittedSeals
	}
	return nil
```
