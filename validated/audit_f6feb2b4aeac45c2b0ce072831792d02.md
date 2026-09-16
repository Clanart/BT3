### Title
Unhandled MultiCall/AddressBook external-call failure during `GetStakingInfo` can permanently halt block finalization for all nodes - (File: kaiax/staking/impl/getter.go)

### Summary
`kaiax/reward` and `kaiax/staking` rely on a system-contract "middleware" (`MultiCallContractCaller`) that performs an external EVM call into the on-chain `AddressBook`/`AddressBookV2` contract (and, transitively, into per-validator `CnStaking` contracts registered there) to build `StakingInfo` for every block. Exactly like the Loihi assimilator pattern described in the report — where an external dependency's unexpected revert bubbles straight up through code that has no fallback — a revert or malformed return from this external call propagates as a hard error out of `GetStakingInfo`, out of `RewardModule.FinalizeState`, and out of `StateProcessor.FinalizeState`/`Process`, which aborts block processing entirely for every node in the network at once.

### Finding Description
`StakingModule.getFromState` builds a `MultiCallContractCaller` and calls `contract.MultiCallStakingInfo` / `contract.MultiCallStakingInfoPermissionless` against the live `AddressBook` state: [1](#0-0) 

Any error returned by these external calls is directly returned as a hard error from `getFromState` → `getFromStateByNumber` → `GetStakingInfo`: [2](#0-1) 

`RewardModule.FinalizeState` (a `kaiax.BlockStateModule`) calls into staking data via `GetDeferredReward`, and if an error is returned it is propagated verbatim: [3](#0-2) 

This `FinalizeState` is invoked by the core `StateProcessor.FinalizeState`, whose failure aborts the entire block `Process()` call with no fallback/partial-success path, unlike the Loihi report's own gasless module (`updateAddresses`), which explicitly treats a broken MultiCall/Registry read as non-fatal ("proceed even if there is something wrong with multicall contract"): [4](#0-3) [5](#0-4) 

Because `AddressBook`/`CnStaking` contract addresses are populated by governance/validator registration (and, since the permissionless staking hardfork (KIP-286/287), CN registration and `CnStaking` contract deployment become far more automatable and validator-driven rather than a small governance-controlled admin set), any state that causes the `MultiCall` contract's internal calls into these registered addresses to revert unexpectedly (e.g. a malformed/self-destructed `CnStaking` contract, an inconsistent AddressBook entry, or a contract that reverts under specific gas/call conditions) makes `MultiCallStakingInfo(Permissionless)` return an error for every node deterministically, since all correctly-running nodes execute the identical call against the identical state. This is not merely a resource-exhaustion/DoS issue — it deterministically prevents the state root of any subsequent block from ever being computed, i.e. it halts state-transition processing / block finalization network-wide (a liveness failure of the whole chain), matching the "whole system locks up" impact described in the source report, just realized at the system-contract layer instead of the assimilator layer.

### Impact Explanation
If `MultiCallStakingInfo`/`MultiCallStakingInfoPermissionless` (or the nested `MultiCallDPStakingInfo` CL-registry read) ever reverts or returns an inconsistent result against legitimately-reachable on-chain state, `GetStakingInfo` errors, `RewardModule.FinalizeState` errors, and `StateProcessor.Process`/`FinalizeState` errors for that block on every full node. Since block production and block validation both go through this same code path, this is a chain-wide liveness halt: no further blocks can be finalized until a hard fork/manual intervention fixes the offending on-chain contract state. This is a High-severity, systemic denial-of-service of the entire network's consensus, not merely a single actor's funds being locked.

### Likelihood Explanation
The likelihood depends on whether an untrusted party can get an adversarial contract registered into the `AddressBook`/`AddressBookV2` in a way that later causes the `MultiCall` read to revert. Historically CN staking registration required governance/validator admin action, limiting this to privileged actors. However, since permissionless staking (KIP-286/287) shifts CN/`CnStaking` registration toward node-operator/validator self-service flows, the barrier to introducing an adversarial or buggy `CnStaking`/reward contract into the address book is lower than before, and the resulting failure mode (an uncaught revert propagating into consensus-critical `FinalizeState`) has no containment or fallback, unlike the sibling `kaiax/gasless` module which explicitly hardened against this exact failure mode. I was not able to fully verify, within the available search budget, the precise validation constraints enforced by the permissionless registration path (`AddressBookV2`/`CnStakingV4Factory`) that would determine exactly which registered contracts can cause `MultiCallStakingInfoPermissionless` to revert; this would need further review of `contracts/system_contracts/AddressBookV2` and `CnStakingV4` sources to confirm exploitability with a fully unprivileged, single transaction.

### Recommendation
Apply the same defensive pattern already used in `kaiax/gasless/impl/getter.go`'s `updateAddresses`: when the `MultiCall`/`AddressBook` external read fails or returns an inconsistent result, do not propagate a hard error out of `FinalizeState`/`Process`. Instead, fall back to an empty/previous known-good `StakingInfo` (as already done for the "AddressBook not installed" case) and log a warning, so block finalization can continue while validators/governance repair the anomalous on-chain data out-of-band. Additionally, consider adding sanity/gas-bounding around the internal `CnStaking`-address enumeration performed by the `MultiCall` contract so a single misbehaving registered contract cannot revert the entire staking-info aggregation call.

### Proof of Concept
A concrete, fully unprivileged single-transaction PoC could not be finalized within the available research: it requires confirming the exact validation rules of the permissionless `CnStaking` registration path (`AddressBookV2`/`CnStakingV4Factory`) to determine whether an attacker-controlled contract can be registered such that `MultiCall.multiCallStakingInfoPermissionless()` (or `multiCallStakingInfo()`/`multiCallDPStakingInfo()`) reverts when called against the resulting state. Conceptually the PoC would be:
1. As an unprivileged actor, use the permissionless registration flow to register a `CnStaking`-role contract that behaves normally on registration but reverts (or consumes disproportionate gas) when its view functions are read back by `MultiCall`'s aggregation logic.
2. Observe that the next `GetStakingInfo(num)` call for the block after registration returns an error on every node.
3. Observe that `RewardModule.FinalizeState` → `StateProcessor.FinalizeState`/`Process` errors out, and no further blocks can be finalized by any honest node running unmodified code, confirming a network-wide liveness halt.

### Citations

**File:** kaiax/staking/impl/getter.go (L48-79)
```go
func (s *StakingModule) GetStakingInfo(num uint64) (*staking.StakingInfo, error) {
	isKaia := s.ChainConfig.IsKaiaForkEnabled(new(big.Int).SetUint64(num))
	sourceNum := sourceBlockNum(num, isKaia, s.stakingInterval)

	// Try cache first
	if si, ok := s.stakingInfoCache.Get(sourceNum); ok {
		return si.(*staking.StakingInfo), nil
	}

	// Only before Kaia, try the database
	if !isKaia {
		if si := ReadStakingInfo(s.ChainKv, sourceNum); si != nil {
			s.stakingInfoCache.Add(sourceNum, si)
			return si, nil
		}
	}

	// Read from the state
	si, err := s.getFromStateByNumber(sourceNum)
	if err != nil {
		return nil, err
	}

	// Only before Kaia, write to database
	if !isKaia {
		WriteStakingInfo(s.ChainKv, sourceNum, si)
	}

	// Cache it
	s.stakingInfoCache.Add(sourceNum, si)
	return si, nil
}
```

**File:** kaiax/staking/impl/getter.go (L115-162)
```go
	// Now we're safe to call the MultiCall contract.
	contract, err := system.NewMultiCallContractCaller(statedb, s.Chain, header)
	if err != nil {
		return nil, staking.ErrMultiCallCall(err)
	}

	callOpts := &bind.CallOpts{BlockNumber: header.Number}

	// Helper to read CL registry info, shared by permissioned and permissionless paths.
	// Permissionless is ordered after Prague (Randao <= Kaia <= Prague <= Permissionless),
	// so permissionless blocks always need CL registry info too.
	readCLInfo := func() (clRegistryResult, error) {
		var clRes clRegistryResult
		// If Registry is not installed, do not handle CL staking info.
		// In private network, Randao and Prague hardfork can be activated at the same block.
		// It leads to staking info inconsistency between block processing and rpc query since the Registry hasn't been installed when finalizing the header.
		// Note that Randao can't be activated after Prague according to fork ordering (Randao <= Kaia <= Prague).
		if statedb.GetCode(system.RegistryAddr) == nil || s.ChainConfig.IsRandaoForkBlockParent(header.Number) {
			logger.Trace("Registry not installed", "sourceNum", num)
			return clRes, nil
		}
		// Note that if CLRegistry is not registered in Registry,
		// it will return empty result and no error.
		clRes, err = contract.MultiCallDPStakingInfo(callOpts)
		if err != nil {
			return clRes, staking.ErrCLRegistryCall(err)
		}
		return clRes, nil
	}

	// Permissionless: read from AddressBookV2 (effective stake, reward-eligible only).
	if isForPermissionless {
		res, err := contract.MultiCallStakingInfoPermissionless(callOpts)
		if err != nil {
			return nil, staking.ErrAddressBookCall(err)
		}
		clRes, err := readCLInfo()
		if err != nil {
			return nil, err
		}
		return parsePermissionlessCallResult(num, res.Profiles, res.StakingAmounts, res.KefAddr, res.KifAddr, res.KpfAddr, clRes)
	}

	// Permissioned: read from legacy AddressBook.
	abRes, err := contract.MultiCallStakingInfo(callOpts)
	if err != nil {
		return nil, staking.ErrAddressBookCall(err)
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

**File:** blockchain/state_processor.go (L125-148)
```go
// FinalizeState runs post-transaction state modifications and assembles final block.
func (p *StateProcessor) FinalizeState(header *types.Header, statedb *state.StateDB, txs []*types.Transaction, receipts types.Receipts) (*types.Block, error) {
	// We can assure that if the magma hard forked block should have the field of base fee
	if p.config.IsMagmaForkEnabled(header.Number) {
		if header.BaseFee == nil {
			logger.Error("Magma hard forked block should have baseFee", "blockNum", header.Number.Uint64())
			return nil, errors.New("Invalid Magma block without baseFee")
		}
	} else if header.BaseFee != nil {
		logger.Error("A block before Magma hardfork shouldn't have baseFee", "blockNum", header.Number.Uint64())
		return nil, ErrInvalidBaseFee
	}

	for _, module := range p.blockStateModules {
		if err := module.FinalizeState(header, statedb, txs, receipts); err != nil {
			return nil, err
		}
	}

	header.Root = statedb.IntermediateRoot(true)

	// Assemble and return the final block for sealing
	return types.NewBlock(header, txs, receipts), nil
}
```

**File:** kaiax/gasless/impl/getter.go (L315-326)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}
```
