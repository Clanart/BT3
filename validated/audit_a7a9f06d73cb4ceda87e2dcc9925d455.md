### Title
Stale in-memory `stateObject` cache in `StateDB.getStateObject` allows precompile-driven balance/storage changes to be silently overwritten on transaction commit - (File: `x/vm/statedb/statedb.go`)

### Summary
This repository (a `cosmos/evm`-family fork) implements the same "cached-context committed to keeper before precompile execution" pattern that caused the original Nibiru H-05 bug. It fixes the *forward* direction (StateDB journal → cacheCtx before running a precompile via `CommitWithCacheCtx`), but the *reverse* direction is still broken: once a `stateObject` for an address has been loaded into `StateDB.stateObjects` (an in-memory Go map), it is never invalidated or refreshed after a precompile call mutates the same address's balance/storage through the cache-context / nested `ApplyMessage` path. Because `getStateObject` always "prefers live objects" from that map over anything in `cacheCtx`/keeper store, subsequent EVM operations in the same transaction (and the final `Commit()`) operate on a stale base state, and can overwrite the precompile-produced state with values computed from data that predates the precompile call.

### Finding Description
`StateDB.getStateObject` is: [1](#0-0) 

It always tries `s.stateObjects[addr]` first, and only falls back to `s.keeper.GetAccount(s.ctx, addr)` on a cache miss. Crucially, this lookup uses `s.ctx` (the outer, tx-level context) — never `s.cacheCtx`, and it is never re-fetched once cached in the map.

The precompile entrypoint (`RunNativeAction` in `precompiles/common/precompile.go`) does the following before invoking precompile logic: [2](#0-1) 

Notably, `stateDB.CommitWithCacheCtx()` (which calls `commitWithCtx(s.cacheCtx)`) is called *before* `action(ctx)` runs, pushing the outer StateDB's journaled dirty state into `cacheCtx`'s KV store so that the precompile sees up-to-date data — this fixes the original "stale read by precompile" half of the bug.

However, when the precompile itself performs a "call contract"-style operation — e.g. `Keeper.CallEVMWithData` — a completely new, independent `StateDB` instance is created: [3](#0-2) [4](#0-3) 

That nested `ApplyMessage`/`ApplyMessageWithConfig` call constructs `statedb.New(ctx, k, txConfig)` — a brand-new `StateDB` with its own empty `stateObjects` map — operating on a further cache-context of the precompile's `cacheCtx`. Any balance/storage mutations it performs are committed into that KV-store layer via its own `commitWithCtx`, and eventually merged back up into `cacheCtx`'s multistore.

The problem: the *outer* transaction-level `StateDB` (the one driving the top-level EVM call/`ethereumTx`) never learns about this. If the outer `StateDB.stateObjects` map already holds a live (possibly dirty) `stateObject` for the address whose balance/storage the precompile just changed (e.g., because the calling contract read `balanceOf()` or otherwise touched that account earlier in the same transaction, which is a common and expected pattern for any contract that calls a precompile and also interacts with the same token/account), any further read (`GetBalance`, `GetState`, etc.) on that address returns the stale in-memory value, and any further write (e.g., a subsequent `transfer()` that does `balance -= amount`) computes its new value on top of that stale base. When `StateDB.Commit()` finally runs: [5](#0-4) 

`commitWithCtx(s.ctx)` iterates `s.journal.sortedDirties()` and calls `s.keeper.SetAccount(ctx, obj.Address(), obj.account)`/`SetState(...)` using the outer, stale `stateObject`, thereby overwriting the correct value that the precompile's nested `ApplyMessage`/keeper calls had just written into the cache-context store. This is functionally identical to the Nibiru root cause described by k-yang: "creating multiple new StateDBs...but resetting the StateDB back to the original StateDB...thereby losing all the storage slot changes in the intermediate StateDBs," compounded by "`StateDB.getStateObject()` always uses the `evmTxCtx` instead of using the `cacheCtx`."

### Impact Explanation
An attacker-controlled contract can:
1. Read/touch an ERC20-representation or precompile-mediated balance (loading it into the outer `StateDB.stateObjects` cache).
2. Call a precompile (e.g., an ERC20/bank/ICS20/staking-style precompile) that internally performs `CallEVMWithData`/`ApplyMessage` and mutates that same account's balance or storage through the nested StateDB/cache-context path (e.g., burns/mints tokens, moves an escrow balance).
3. Perform another EVM-level operation on the same account (even a trivial dust transfer or storage write) so the stale, pre-precompile-call `stateObject` becomes dirtied again in the outer journal.
4. On commit, the outer StateDB overwrites the precompile's correct state change with a value computed from the stale pre-call state, effectively double-crediting funds to the contract (duplication of spendable value) while the precompile's side effect (e.g., bank mint/burn or IBC escrow movement) has already been applied and cannot be reversed.

This maps directly to the "Critical unauthorized minting/duplication/irreversible accounting corruption of spendable user value" impact class: token-pair-backed balances (ERC20 representations of native coins, IBC-escrowed value, or precompile-mediated balances) can be duplicated or corrupted, enabling unlimited-mint-style exploits analogous to the original PoC (`sendToBank` value duplication).

### Likelihood Explanation
The trigger requires only an unprivileged user deploying an ordinary smart contract that touches an account/storage slot before and after invoking a precompile that performs an internal `CallEVMWithData`/`ApplyMessage`-based operation on the same account — this is a normal, permissionless pattern (no special privileges, validator, or relayer access needed), matching the original PoC's structure closely. Confidence is moderate-to-high based on the code paths examined (`getStateObject`'s stale-cache-preference, the nested `StateDB` creation in `CallEVMWithData`, and `commitWithCtx` unconditionally re-writing outer journal dirty entries), but I was not able to fully inspect `state_object.go`'s `SetState`/dirty-storage base-value handling in the final iteration (tool access ended before I could confirm exactly how `dirtyStorage`/`originStorage` are seeded relative to a stale object), so the precise mechanics of which specific precompile(s) in this codebase actually invoke `CallEVMWithData` from within a `RunNativeAction` context (as opposed to only calling bank/erc20 keeper methods directly against the passed-in `ctx`) should be re-verified in a live session to confirm an end-to-end exploit path with concrete numbers.

### Recommendation
- After a precompile's `NativeAction` returns (in `precompiles/common/precompile.go:runNativeAction`) and after any nested `ApplyMessage`/`CallEVMWithData` call, invalidate or refresh any `StateDB.stateObjects` entries for addresses that were touched by the precompile/nested call, forcing subsequent `getStateObject` calls to reload from the updated `cacheCtx`/keeper state rather than serving stale in-memory objects.
- Alternatively, ensure a single `StateDB` instance (with a single `stateObjects` map) is threaded through all nested `CallEVM`/`ApplyMessage` invocations triggered from within a precompile, rather than constructing an independent `statedb.New(...)` per nested call, so that all state mutations are visible to and originate from the same journal/object cache.
- Add an invariant/regression test replicating the original PoC pattern (read balance → call precompile that mutates it internally → perform a further EVM operation on the same account → commit) to assert no value duplication/loss occurs.

### Proof of Concept
Conceptual reproduction (adapted from the original Nibiru PoC, not yet executed against this repo):
1. Deploy a contract that holds an ERC20-style token balance backed by a token-pair/precompile-mediated asset.
2. In a single call: (a) read the contract's own token balance (forcing the outer `StateDB` to load and cache the corresponding `stateObject`), (b) invoke a precompile method that internally triggers `Keeper.CallEVMWithData`/`ApplyMessage` to reduce that balance and credit an escrow/native-bank account, (c) perform a subsequent trivial transfer/write on the same token contract to re-dirty the (stale) cached `stateObject`.
3. Observe that after the transaction commits, the token contract's on-chain balance reflects the pre-precompile-call value (or an inconsistent value) while the precompile's side effect (e.g., minted/escrowed native coins) has already been applied — resulting in a net duplication of value.

I was unable to execute this PoC within the current session; it requires setting up a local node/test harness with a concrete precompile that performs a `CallEVMWithData`-based internal call, which should be done in a dedicated Devin session with full repository and terminal access to confirm the exact numeric duplication.

### Citations

**File:** x/vm/statedb/statedb.go (L348-364)
```go
// getStateObject retrieves a state object given by the address, returning nil if
// the object is not found.
func (s *StateDB) getStateObject(addr common.Address) *stateObject {
	// Prefer live objects if any is available
	if obj := s.stateObjects[addr]; obj != nil {
		return obj
	}
	// If no live objects are available, load it from keeper
	account := s.keeper.GetAccount(s.ctx, addr)
	if account == nil {
		return nil
	}
	// Insert into the live set
	obj := newObject(s, addr, *account)
	s.setStateObject(obj)
	return obj
}
```

**File:** x/vm/statedb/statedb.go (L695-745)
```go
// Commit writes the dirty states to keeper
// the StateDB object should be discarded after committed.
func (s *StateDB) Commit() error {
	// writeCache func will exist only when there's a call to a precompile.
	// It applies all the store updates preformed by precompile calls.
	if s.writeCache != nil {
		s.writeCache()
	}
	return s.commitWithCtx(s.ctx)
}

// CommitWithCacheCtx writes the dirty states to keeper using the cacheCtx.
// This function is used before any precompile call to make sure the cacheCtx
// is updated with the latest changes within the tx (StateDB's journal entries).
func (s *StateDB) CommitWithCacheCtx() error {
	return s.commitWithCtx(s.cacheCtx)
}

// commitWithCtx writes the dirty states to keeper
// using the provided context
func (s *StateDB) commitWithCtx(ctx sdk.Context) error {
	for _, addr := range s.journal.sortedDirties() {
		obj := s.stateObjects[addr]
		if obj.selfDestructed {
			if err := s.keeper.DeleteAccount(ctx, obj.Address()); err != nil {
				return errorsmod.Wrapf(err, "failed to delete account %s", obj.Address())
			}
		} else {
			if obj.code != nil && obj.dirtyCode {
				if len(obj.code) == 0 {
					s.keeper.DeleteCode(ctx, obj.CodeHash())
				} else {
					s.keeper.SetCode(ctx, obj.CodeHash(), obj.code)
				}
			}
			if err := s.keeper.SetAccount(ctx, obj.Address(), obj.account); err != nil {
				return errorsmod.Wrap(err, "failed to set account")
			}

			for _, key := range obj.dirtyStorage.SortedKeys() {
				valueBytes := obj.dirtyStorage[key].Bytes()
				if len(valueBytes) == 0 {
					s.keeper.DeleteState(ctx, obj.Address(), key)
				} else {
					s.keeper.SetState(ctx, obj.Address(), key, valueBytes)
				}
			}
		}
	}
	return nil
}
```

**File:** precompiles/common/precompile.go (L57-97)
```go
func (p Precompile) runNativeAction(evm *vm.EVM, contract *vm.Contract, action NativeAction) (bz []byte, err error) {
	stateDB, ok := evm.StateDB.(*statedb.StateDB)
	if !ok {
		return nil, errors.New(ErrNotRunInEvm)
	}

	// get the stateDB cache ctx
	ctx, err := stateDB.GetCacheContext()
	if err != nil {
		return nil, err
	}

	// take a snapshot of the current state before any changes
	// to be able to revert the changes
	snapshot := stateDB.MultiStoreSnapshot()
	events := ctx.EventManager().Events()

	// add precompileCall entry on the stateDB journal
	// this allows to revert the changes within an evm tx
	if err := stateDB.AddPrecompileFn(snapshot, events); err != nil {
		return nil, err
	}

	// commit the current changes in the cache ctx
	// to get the updated state for the precompile call
	if err := stateDB.CommitWithCacheCtx(); err != nil {
		return nil, err
	}

	initialGas := ctx.GasMeter().GasConsumed()

	defer HandleGasError(ctx, contract, initialGas, &err)()

	// set the default SDK gas configuration to track gas usage
	// we are changing the gas meter type, so it panics gracefully when out of gas
	ctx = ctx.WithGasMeter(storetypes.NewGasMeter(contract.Gas)).
		WithKVGasConfig(p.KvGasConfig).
		WithTransientKVGasConfig(p.TransientKVGasConfig)

	// we need to consume the gas that was already used by the EVM
	ctx.GasMeter().ConsumeGas(initialGas, "creating a new gas meter")
```

**File:** x/vm/keeper/call_evm.go (L75-92)
```go
	// Use a cache context so that a reverting EVM call does not corrupt the
	// parent gas meter. On success we commit the cache and charge the actual
	// gas used; on revert we discard the cache and leave the parent meter
	// untouched (matching DerivedEVMCallWithData semantics).
	tmpCtx, commitState := ctx.CacheContext()
	res, err := k.ApplyMessage(tmpCtx, msg, nil, commit, true)
	if err != nil {
		return nil, err
	}

	if res.Failed() {
		return res, errorsmod.Wrap(types.ErrVMExecution, res.VmError)
	}

	commitState()
	ctx.GasMeter().ConsumeGas(res.GasUsed, "apply evm message")

	return res, nil
```

**File:** x/vm/keeper/state_transition.go (L386-401)
```go
func (k *Keeper) ApplyMessageWithConfig(
	ctx sdk.Context,
	msg core.Message,
	tracer *tracing.Hooks,
	commit bool,
	cfg *statedb.EVMConfig,
	txConfig statedb.TxConfig,
	internal bool,
	overrides *rpctypes.StateOverride,
) (*types.MsgEthereumTxResponse, error) {
	var (
		ret   []byte // return bytes from evm execution
		vmErr error  // vm errors do not effect consensus and are therefore not assigned to err
	)

	stateDB := statedb.New(ctx, k, txConfig)
```
