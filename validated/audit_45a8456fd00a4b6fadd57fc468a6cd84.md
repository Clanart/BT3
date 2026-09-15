### Title
Staking/CL data retrieval is not fail-safe against a single reverting external contract call, risking chain-wide block-finalization failure - ([File: kaiax/staking/impl/getter.go])

### Summary
`RootManager.propagate`'s bug class — a single external-call failure inside a loop cascading into total system failure — has a structural analog in Kaia's `StakingModule.getFromState`/`readCLInfo` flow. This function makes sequential, unguarded external `Call`s to system contracts (`MultiCallContract.MultiCallStakingInfo` / `MultiCallStakingInfoPermissionless` / `MultiCallDPStakingInfo`) and returns the first error verbatim with no fallback, retry, or partial-result handling. Because `GetStakingInfo` is invoked from `RewardModule.FinalizeState` on **every block**, a revert in any single one of these calls stalls reward distribution and thus block finalization network-wide.

### Finding Description
`StakingModule.getFromState` builds a `MultiCallContract` caller against the `AddressBook`/`Registry` system contracts and issues external calls: [1](#0-0) 

For CL (Consensus Liquidity) staking information, introduced by KIP-226, it performs an additional external call to the CL registry via `MultiCallDPStakingInfo`, and any error is propagated immediately without any recovery path: [2](#0-1) 

Both the permissionless and permissioned code paths short-circuit and return an error the instant any one of these external calls fails: [3](#0-2) 

This `GetStakingInfo` result feeds directly into `RewardModule.FinalizeState`, which executes at the end of **every block**'s processing to distribute deferred rewards: [4](#0-3) 

If `GetDeferredReward`/`GetStakingInfo` returns an error, `FinalizeState` returns that error, which aborts block finalization. Unlike Connext's `RootManager.propagate` (which loops over six independent bridge AMB calls with no try/catch), Kaia's flow loops/sequences over calls to contracts that are not fully under core-protocol control: the `AddressBook`'s registered CN staking contracts and, since Prague/KIP-226, permissionlessly-registerable CL (Consensus Liquidity) staking pools recorded in the on-chain `Registry`. There is no per-entry isolation (e.g., skip-and-continue with a default value) analogous to the fix Connext adopted (try/catch around each connector call) — a single failing/reverting registrant poisons the entire aggregate read used by every subsequent block.

### Impact Explanation
Because `StakingInfo` is consumed unconditionally in `FinalizeState` for every block (both Kore/KIP-82 and Prague/flex reward paths), an unrecoverable error from any one of the chained external calls (`MultiCallStakingInfo`, `MultiCallStakingInfoPermissionless`, `MultiCallDPStakingInfo`) causes block finalization to fail across the network — a systemic denial-of-service that halts reward distribution and, by extension, block production/finalization for the entire chain (not merely one feature), which is a materially graver consequence than the original Connext report describes for a single bridge route. This matches the "state divergence between honest nodes / acceptance of an invalid block" and "no fail-safe against third-party/external call failure" bug class explicitly called out in the source report.

### Likelihood Explanation
The likelihood hinges on whether an entity outside core-protocol control (e.g., an operator registering a CL staking pool under KIP-226's permissionless CL registration, or a CN operator's staking-contract implementation) can cause the `MultiCallContract`'s underlying calls to revert. The Go-side error-handling defect (no isolation/fallback around each external call) is directly verified in `kaiax/staking/impl/getter.go`. However, I could not verify the exact Solidity implementation of `MultiCallContract`/`multiCallDPStakingInfo` in the production `kaia-system-contracts` package (only test mocks and Go bindings were present in the indexed codebase), so I cannot confirm with certainty whether the per-CL-pool/per-CN external subcalls inside that Solidity contract are also unguarded (e.g., lack `try/catch` around each node's staking-amount lookup) and whether such registration is reachable by a truly unprivileged public actor versus requiring governance/GC approval. This should be verified against the actual system-contract source before assigning a final severity, as it determines whether the trigger is "permissionless" (higher likelihood, Critical) or restricted to governance-vetted validators (Medium likelihood).

### Recommendation
Make `getFromState`/`readCLInfo` fail-safe:
- Wrap each external system-contract call (`MultiCallStakingInfo`, `MultiCallStakingInfoPermissionless`, `MultiCallDPStakingInfo`) so that a failure of one component (e.g., CL registry or a specific registered pool) does not abort the entire `StakingInfo`/reward computation.
- Where per-node/per-pool amounts are aggregated (either in Go or inside the underlying Solidity `MultiCallContract`), use isolated calls (e.g., `staticcall` with success-check, or Solidity `try/catch`) per entry, defaulting a failing entry's contribution to zero/excluded rather than reverting/erroring the whole aggregate.
- Add regression tests simulating a reverting CL pool / staking contract registered in the `AddressBook`/`Registry` to confirm block finalization proceeds using the remaining valid entries.

### Proof of Concept
Conceptual sequence (Go-side, verified):
1. A CL staking pool (or CN staking contract) referenced by the on-chain `Registry`/`AddressBook` is implemented (or upgraded, where upgradeable) such that its balance/view function invoked internally by `MultiCallDPStakingInfo` (or `MultiCallStakingInfo`) reverts.
2. Any full node calling `StakingModule.GetStakingInfo(num)` → `getFromState` → `contract.MultiCallDPStakingInfo(callOpts)` receives an error: [5](#0-4) 
3. That error propagates to `RewardModule.FinalizeState`: [6](#0-5) 
4. Block finalization for that block fails on every node evaluating it, halting further block processing chain-wide until the offending registrant is manually removed from the `Registry`/`AddressBook` — mirroring exactly the "one hub connector reverts, entire `propagate` breaks, network halts until manual fix" failure mode described in the source report.

Note: Step 1's exact feasibility (whether a fully unprivileged actor can register such a contract, and whether the Solidity `MultiCallContract` itself further isolates per-entry failures) could not be confirmed because the production Solidity source for `MultiCallContract`/CL registry is not present in the indexed codebase (only Go bindings and test mocks were available). This should be validated directly against `kaia-system-contracts` source before treating this as fully confirmed at Critical severity.

### Citations

**File:** kaiax/staking/impl/getter.go (L115-120)
```go
	// Now we're safe to call the MultiCall contract.
	contract, err := system.NewMultiCallContractCaller(statedb, s.Chain, header)
	if err != nil {
		return nil, staking.ErrMultiCallCall(err)
	}

```

**File:** kaiax/staking/impl/getter.go (L123-143)
```go
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
```

**File:** kaiax/staking/impl/getter.go (L145-170)
```go
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

	var clRes clRegistryResult
	if isForPrague {
		clRes, err = readCLInfo()
		if err != nil {
			return nil, err
		}
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
