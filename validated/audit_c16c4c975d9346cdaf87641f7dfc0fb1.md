## Analysis

Kaia's post-Prague "permissionless" validator system (`AddressBookV2` at `0x400`) exposes `createNode()` as a public, paid mutator transaction — any account that has deployed a `CnStakingV4` (via the permissionless `CnStakingV4Factory.deployCnStaking()`, also public) can call `createNode(nodeId, stakingContract, rewardAddress, voterAddress, blsInfo, name, metadata, nodeIdSig)` to register itself as a validator node, entering the `NodeMap` in a non-`ValActive` state (`Registered`/`CandReady`) even without meeting `MinStake` for the active competition. [1](#0-0) [2](#0-1) 

Every block, `kaiax/valset` reads the full `NodeMap` from ABv2 state and runs `TransitionContext.ApplyAllTransitions`, which calls `applyViolationTransition` (explicitly documented as "Runs every block") and, on epoch boundaries, `applyEpochTransition`. Both functions iterate over **every entry of the NodeMap**, not just `ValActive` ones:
- `applyEpochTransition` does `for addr, val := range newValidators` over the whole map to route `CandReady/CandTesting/ValReady/ValActive/ValPaused/ValExiting` states. [3](#0-2) 
- `applyViolationTransition` computes `sortedAddrs := newValidators.Addresses()` and iterates it every block, plus calls `newValidators.CountByState(...)` (also O(N)) inside `hasSlot`/`canDemoteActive` closures invoked per iteration. [4](#0-3) 

Since `createNode` is unauthenticated/permissionless (any address with a deployed CnStaking contract and a valid `nodeIdSig` can call it, with no cap enforced at the Go/orchestrator layer visible in this codebase slice — only `MaxNodeCount`/`MaxCandReadyCount` config values exist as governance-settable but not necessarily hard-capped at protocol level for `Registered` state), an attacker can submit a large number of `createNode` transactions to inflate the `NodeMap` to an arbitrary size. Because `applyViolationTransition` runs on **every block** (not just epoch boundaries) and does multiple full-map scans (`sortedAddrs` build, per-state loops, `CountByState` calls), this directly parallels the `plugins[]` unbounded-loop pattern from the reference report: an ever-growing, permissionlessly-extendable array that is walked on every block's state transition, degrading or threatening to exceed block-processing budgets for all nodes syncing/validating the chain.

This differs from the CN-staking reward-distribution loops (`ConsolidatedNodes()`, `assignStakingRewards`) which are filtered to `ValActive` nodes only (bounded by `MaxValActivePausedCount`/slot math) and are therefore not vulnerable to this class of growth — confirmed by `TestGetStakingInfo_Permissionless_OnlyValActive` showing only `ValActive` nodes flow into `StakingInfo`. [5](#0-4) 

I could not locate, within the indexed portion of this codebase, an explicit hard cap enforced in Go (`kaiax/valset` or `blockchain/system`) on total `NodeMap` size independent of the `MaxNodeCount` governance parameter's on-chain (Solidity) enforcement — the `MaxNodeCount`/`MaxCandReadyCount` values are set in `ABv2DataContractInitData` as governance parameters but their enforcement logic lives in the `AddressBookV2`/`CnStakingV4` Solidity contracts, which are referenced only as bindings here and not fully visible in this index. If `createNode` in the actual Solidity contract does enforce `MaxNodeCount` before insertion, the severity would be capped by that parameter's value (which could still be set high enough for a practical DoS) rather than being truly unbounded.

### Title
Permissionless `AddressBookV2.createNode()` allows unbounded NodeMap growth looped every block in `applyViolationTransition` - (File: kaiax/valset/impl/transition_context.go)

### Summary
`AddressBookV2.createNode()` is a public, unauthenticated transaction that lets any address register a new validator node into the on-chain `NodeMap`, subject only to a governance-configured `MaxNodeCount`. The full `NodeMap` (all states, not just `ValActive`) is read and fully iterated by `kaiax/valset`'s `TransitionContext.applyViolationTransition` on **every single block**, and by `applyEpochTransition` on every epoch, mirroring the "unbounded array looped frequently" bug class from the referenced Telcoin `plugins[]` finding.

### Finding Description
`createNode` is exposed as an ordinary paid transaction to `AddressBookV2` at `0x400`, requiring only a deployed CnStaking contract and a valid `nodeIdSig` — no staking-amount or admin gating is visible at this call. [1](#0-0) 
The `CnStakingV4Factory.deployCnStaking` used to obtain a staking contract is likewise a public mutator, callable by any account. [2](#0-1) 

Once registered, a node lands in the `NodeMap` (e.g. `Registered` or `CandReady` state) and remains part of the map indefinitely unless deleted. Every block, the orchestrator invokes `ApplyAllTransitions`, which unconditionally calls `applyViolationTransition(nodes)` — documented as running "every block" — and this function:
1. Builds `sortedAddrs := newValidators.Addresses()` (O(N) over the whole map),
2. Runs a `for _, state := range [...] { for _, addr := range sortedAddrs { ... } }` double loop over all states and all addresses,
3. Invokes `hasSlot`/`canDemoteActive` closures that call `newValidators.CountByState(state)`, itself an O(N) scan, from within the per-address loop. [4](#0-3) 

On epoch blocks, `applyEpochTransition` additionally performs a `for addr, val := range newValidators` pass over the entire map plus a sort of all active competitors. [6](#0-5) 

Because node registration is reachable from an ordinary transaction, an attacker can inflate `len(NodeMap)` by repeatedly deploying minimal CnStaking contracts and calling `createNode`, causing the per-block cost of `applyViolationTransition` (and the periodic cost of `applyEpochTransition`) to grow linearly (with nested `CountByState` calls making it effectively worse than O(N) per block).

### Impact Explanation
Unlike the reward-distribution path (`ConsolidatedNodes()`/`assignStakingRewards`), which is filtered to `ValActive` nodes and thus bounded by consensus-slot math, the violation-transition loop processes the raw, unfiltered `NodeMap` on **every block**. If registration is not hard-capped at a small value, this becomes a chain-wide state-transition cost that scales with attacker-controlled input, risking degraded block processing time for every full node and potentially divergent behavior/timeouts across nodes with different resource budgets — a state-divergence and liveness risk analogous to the original `plugins[]` DoS.

### Likelihood Explanation
Likelihood depends entirely on whether `MaxNodeCount`/`MaxCandReadyCount` are enforced tightly on-chain (in the Solidity `AddressBookV2`/`CnStakingV4Factory` contracts, not fully visible in this index) and how expensive it is to deploy and register a new node (staking contract deployment cost + minimal stake, if any, required for `Registered`/`CandReady` states). If registration in a non-competing state (`Registered`) requires no minimum stake, the cost to grow the `NodeMap` is just gas for `deployCnStaking` + `createNode`, making this cheaply repeatable.

### Recommendation
- Enforce a hard, protocol-level cap on total `NodeMap` size (all states combined) at `createNode()` time in the `AddressBookV2` contract, not just a governance-adjustable soft limit.
- In `kaiax/valset/impl/transition_context.go`, avoid repeated O(N) `CountByState` calls inside per-address loops in `applyViolationTransition` — maintain running counters instead.
- Consider excluding/pruning long-idle `Registered`/non-competing nodes from the per-block violation-transition scan, restricting the every-block loop to only `ValActive`/`ValPaused`/`ValReady` (the actually consensus-relevant subset), similar to how the reward module already filters to `ValActive` only.

### Proof of Concept
1. An attacker deploys N minimal `CnStakingV4` proxies via `CnStakingV4Factory.deployCnStaking(owner)` (public function), each requiring only trivial/no stake to exist as a contract. [2](#0-1) 
2. For each proxy, the attacker calls `AddressBookV2.createNode(nodeId, stakingContract, rewardAddress, voterAddress, blsInfo, name, metadata, nodeIdSig)` with a freshly generated `nodeId` key signing `nodeIdSig`, registering N new entries into the `NodeMap` in `Registered`/`CandReady` state. [1](#0-0) 
3. On every subsequent block, `kaiax/valset`'s orchestrator calls `ApplyAllTransitions` → `applyViolationTransition`, which scans all N+existing entries via `sortedAddrs := newValidators.Addresses()` and repeatedly calls `newValidators.CountByState(...)` inside the per-address loop, and — on epoch boundaries — `applyEpochTransition` performs another full-map pass. [7](#0-6) 
4. As N grows (bounded only by gas cost of registration transactions and any on-chain `MaxNodeCount`/`MaxCandReadyCount` check not visible in this Go-side index), per-block validator state-transition cost grows correspondingly, degrading block-processing performance network-wide.

### Citations

**File:** contracts/bindings/addressbookv2/AddressBookV2.go (L1635-1654)
```go
// CreateNode is a paid mutator transaction binding the contract method 0x53d39bfb.
//
// Solidity: function createNode(address nodeId, address stakingContract, address rewardAddress, address voterAddress, (bytes,bytes) blsInfo, string name, string metadata, bytes nodeIdSig) returns()
func (_AddressBookV2 *AddressBookV2Transactor) CreateNode(opts *bind.TransactOpts, nodeId common.Address, stakingContract common.Address, rewardAddress common.Address, voterAddress common.Address, blsInfo BlsPublicKeyInfo, name string, metadata string, nodeIdSig []byte) (*types.Transaction, error) {
	return _AddressBookV2.contract.Transact(opts, "createNode", nodeId, stakingContract, rewardAddress, voterAddress, blsInfo, name, metadata, nodeIdSig)
}

// CreateNode is a paid mutator transaction binding the contract method 0x53d39bfb.
//
// Solidity: function createNode(address nodeId, address stakingContract, address rewardAddress, address voterAddress, (bytes,bytes) blsInfo, string name, string metadata, bytes nodeIdSig) returns()
func (_AddressBookV2 *AddressBookV2Session) CreateNode(nodeId common.Address, stakingContract common.Address, rewardAddress common.Address, voterAddress common.Address, blsInfo BlsPublicKeyInfo, name string, metadata string, nodeIdSig []byte) (*types.Transaction, error) {
	return _AddressBookV2.Contract.CreateNode(&_AddressBookV2.TransactOpts, nodeId, stakingContract, rewardAddress, voterAddress, blsInfo, name, metadata, nodeIdSig)
}

// CreateNode is a paid mutator transaction binding the contract method 0x53d39bfb.
//
// Solidity: function createNode(address nodeId, address stakingContract, address rewardAddress, address voterAddress, (bytes,bytes) blsInfo, string name, string metadata, bytes nodeIdSig) returns()
func (_AddressBookV2 *AddressBookV2TransactorSession) CreateNode(nodeId common.Address, stakingContract common.Address, rewardAddress common.Address, voterAddress common.Address, blsInfo BlsPublicKeyInfo, name string, metadata string, nodeIdSig []byte) (*types.Transaction, error) {
	return _AddressBookV2.Contract.CreateNode(&_AddressBookV2.TransactOpts, nodeId, stakingContract, rewardAddress, voterAddress, blsInfo, name, metadata, nodeIdSig)
}
```

**File:** contracts/bindings/cnstakingv4factory/CnStakingV4Factory.go (L433-452)
```go
// DeployCnStaking is a paid mutator transaction binding the contract method 0x4ed7a764.
//
// Solidity: function deployCnStaking(address _owner) returns(address cnStakingProxy)
func (_CnStakingV4Factory *CnStakingV4FactoryTransactor) DeployCnStaking(opts *bind.TransactOpts, _owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4Factory.contract.Transact(opts, "deployCnStaking", _owner)
}

// DeployCnStaking is a paid mutator transaction binding the contract method 0x4ed7a764.
//
// Solidity: function deployCnStaking(address _owner) returns(address cnStakingProxy)
func (_CnStakingV4Factory *CnStakingV4FactorySession) DeployCnStaking(_owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4Factory.Contract.DeployCnStaking(&_CnStakingV4Factory.TransactOpts, _owner)
}

// DeployCnStaking is a paid mutator transaction binding the contract method 0x4ed7a764.
//
// Solidity: function deployCnStaking(address _owner) returns(address cnStakingProxy)
func (_CnStakingV4Factory *CnStakingV4FactoryTransactorSession) DeployCnStaking(_owner common.Address) (*types.Transaction, error) {
	return _CnStakingV4Factory.Contract.DeployCnStaking(&_CnStakingV4Factory.TransactOpts, _owner)
}
```

**File:** kaiax/valset/impl/transition_context.go (L171-225)
```go
func (ctx *TransitionContext) applyEpochTransition(m valset.NodeMap) valset.NodeMap {
	// sortableValidator pairs an address with its mutable state so the epoch
	// transition can sort competitors by stake while still mutating their fields.
	type sortableValidator struct {
		addr common.Address
		*valset.Node
	}

	var (
		newValidators        = m.Copy()
		activeValCompetitors []sortableValidator
		// Captured before the loop mutates states.
		prevActive = newValidators.FilterByState(valset.ValActive).Addresses()
	)

	// T3a/T3b: compete for top-N or demote to ValInactive.
	// Shared by EpochCompetitors = {VA, VR, VP, CT}.
	competeOrDemote := func(addr common.Address, val *valset.Node) {
		if val.StakingAmount >= ctx.MinStake {
			activeValCompetitors = append(activeValCompetitors, sortableValidator{addr, val}) // T3a
		} else {
			if val.State != valset.ValReady {
				val.IdleTimeout = ctx.BlockTime.Add(ctx.IdleTimeout)
			}
			val.State = valset.ValInactive // T3b
		}
	}
	for addr, val := range newValidators {
		switch val.State {
		case valset.ValExiting:
			val.State = valset.ValInactive // T1
			val.IdleTimeout = ctx.BlockTime.Add(ctx.IdleTimeout)
		case valset.CandReady:
			if val.StakingAmount >= ctx.MinStake {
				val.State = valset.CandTesting // T4a
			} else {
				val.State = valset.Registered // T4b
			}
		case valset.CandTesting:
			if ctx.isPassVrankTest(addr) {
				competeOrDemote(addr, val)
			} else {
				logger.Trace("VRank test failed: CandTesting → Registered", "addr", addr, "cfs", ctx.CFS[addr], "cfsThreshold", ctx.CfsThreshold)
				val.State = valset.Registered // T2
			}
		case valset.ValReady, valset.ValActive, valset.ValPaused:
			competeOrDemote(addr, val)
		}
	}
	slices.SortFunc(activeValCompetitors, func(a, b sortableValidator) int {
		return cmp.Or(
			cmp.Compare(b.StakingAmount, a.StakingAmount),
			bytes.Compare(b.addr[:], a.addr[:]), // tie-breaking: higher address first, per KIP-286
		)
	})
```

**File:** kaiax/valset/impl/transition_context.go (L281-332)
```go
func (ctx *TransitionContext) applyViolationTransition(m valset.NodeMap) valset.NodeMap {
	var (
		newValidators = m.Copy()
		// Slot/count helpers check the in-progress state of newValidators.
		// Counts change as validators transition within the loop, so these cannot be replaced with a contract call.
		hasSlot = func(state valset.NodeState) bool {
			return newValidators.CountByState(state) < ctx.MaxSlotAvailable
		}
		// canDemoteActive additionally ensures enough ValActive remain for consensus.
		// Used only when transitioning FROM ValActive (reducing active count).
		canDemoteActive = func(targetState valset.NodeState) bool {
			return hasSlot(targetState) && newValidators.CountByState(valset.ValActive) > ctx.MinActiveCount
		}
	)

	// Iterate in deterministic address order. Slot-limited transitions depend on
	// which validator is processed first, so random map iteration would be nondeterministic.
	sortedAddrs := newValidators.Addresses()

	// rule1: staking amount dropped below MinimumStake.
	// Pass per source state: ValActive and ValPaused both draw on the ValExiting slots,
	// and ValActive additionally draws on the active floor, so it is served first.
	for _, state := range []valset.NodeState{valset.ValActive, valset.ValPaused, valset.ValReady} {
		for _, addr := range sortedAddrs {
			val := newValidators[addr]
			if val.State != state || val.StakingAmount >= ctx.MinStake {
				continue
			}
			switch val.State {
			case valset.ValActive:
				// ValActive → ValExiting (slot + minActiveCount: removing an active validator reduces consensus participants)
				if canDemoteActive(valset.ValExiting) {
					logger.Trace("MinStake violation: ValActive → ValExiting", "addr", addr, "staking", val.StakingAmount, "minStake", ctx.MinStake)
					val.State = valset.ValExiting
				} else {
					logger.Trace("MinStake violation: slot full, skipping ValActive transition", "addr", addr, "staking", val.StakingAmount)
				}
			case valset.ValPaused:
				// ValPaused → ValExiting (slot only: ValPaused is already not in active set)
				if hasSlot(valset.ValExiting) {
					logger.Trace("MinStake violation: ValPaused → ValExiting", "addr", addr, "staking", val.StakingAmount, "minStake", ctx.MinStake)
					val.State = valset.ValExiting
				} else {
					logger.Trace("MinStake violation: slot full, skipping ValPaused transition", "addr", addr, "staking", val.StakingAmount)
				}
			case valset.ValReady:
				// ValReady → ValInactive (no slot check, not in active set)
				logger.Trace("MinStake violation: ValReady → ValInactive", "addr", addr, "staking", val.StakingAmount, "minStake", ctx.MinStake)
				val.State = valset.ValInactive
			}
		}
	}
```

**File:** kaiax/staking/impl/getter_test.go (L285-331)
```go
func TestGetStakingInfo_Permissionless_OnlyValActive(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)

	// MultiCallContractMockPermissionless.multiCallStakingInfoPermissionless returns 6 profiles:
	//   [0] node=0xF00  staking=0xF01  reward=0xF02  ValActive    5M  <- kept
	//   [1] node=0xF03  staking=0xF04  reward=0xF05  ValPaused   10M
	//   [2] node=0x1000 staking=0x1001 reward=0x1002 ValReady     8M
	//   [3] node=0x2000 staking=0x2001 reward=0x2002 ValExiting   7M
	//   [4] node=0x3000 staking=0x3001 reward=0x3002 CandReady    6M
	//   [5] node=0x4000 staking=0x4001 reward=0x4002 ValActive    9M  <- kept
	// Only the two ValActive entries are reward-eligible (KIP-286).
	originCode := system.MultiCallCode
	system.MultiCallCode = system.MultiCallPermlessMockCode
	defer func() { system.MultiCallCode = originCode }()

	db := database.NewMemoryDBManager()
	config := testPragueForkChainConfig(big.NewInt(0))
	config.PermissionlessCompatibleBlock = big.NewInt(0)

	alloc := blockchain.GenesisAlloc{
		system.AddressBookAddr: {Code: []byte{0x01}, Balance: big.NewInt(0)}, // non-nil to pass bail-out check
	}
	backend := backends.NewSimulatedBackendWithDatabase(db, alloc, config)

	mStaking := NewStakingModule()
	mStaking.Init(&InitOpts{
		ChainKv:     db.GetMiscDB(),
		ChainConfig: config,
		Chain:       backend.BlockChain(),
	})
	si, err := mStaking.GetStakingInfo(0)
	assert.NoError(t, err)

	// ValPaused / ValReady / ValExiting / CandReady are all excluded.
	assert.Len(t, si.NodeIds, 2)
	assert.Equal(t, common.HexToAddress("0xF00"), si.NodeIds[0])
	assert.Equal(t, common.HexToAddress("0x4000"), si.NodeIds[1])
	assert.Equal(t, common.HexToAddress("0xF01"), si.StakingContracts[0])
	assert.Equal(t, common.HexToAddress("0x4001"), si.StakingContracts[1])
	assert.Equal(t, common.HexToAddress("0xF02"), si.RewardAddrs[0])
	assert.Equal(t, common.HexToAddress("0x4002"), si.RewardAddrs[1])
	assert.Equal(t, uint64(5_000_000), si.StakingAmounts[0])
	assert.Equal(t, uint64(9_000_000), si.StakingAmounts[1])
	assert.Equal(t, common.HexToAddress("0x0a01"), si.KEFAddr)
	assert.Equal(t, common.HexToAddress("0x0a02"), si.KIFAddr)
	assert.Equal(t, common.HexToAddress("0x0a03"), si.KPFAddr)
}
```
