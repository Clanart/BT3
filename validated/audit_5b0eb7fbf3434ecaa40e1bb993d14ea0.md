### Title
`selfDestructChange.Revert` Fails to Restore Burned Balance, Enabling Permanent Fund Drain via Reverted Sub-call — (File: `x/evm/statedb/journal.go`)

---

### Summary

Ethermint's `selfDestructChange.Revert` restores only the `selfDestructed` flag but never restores the balance that `SelfDestruct` immediately burned via `SubBalance`. Because Ethermint has no `balanceChange` journal entry (unlike go-ethereum), and because bank-module writes go directly to the branched `cacheMS` without journal tracking, a snapshot revert after `SelfDestruct` leaves the account's balance permanently at zero. An unprivileged attacker can exploit this to permanently drain any contract's EVM-denom balance by triggering `SELFDESTRUCT` inside a sub-call and then reverting the outer call.

---

### Finding Description

**Root cause — `selfDestructChange.Revert` ignores `prevbalance`:**

`SelfDestruct` in `x/evm/statedb/statedb.go` does two things atomically:

1. Appends a `selfDestructChange` journal entry that captures `prevbalance`.
2. Immediately calls `SubBalance` to burn the account's balance through the bank module via `s.ctx` (which holds the branched `cacheMS`). [1](#0-0) 

The journal entry stores `prevbalance`: [2](#0-1) 

But `selfDestructChange.Revert` only restores the flag — it never uses `prevbalance`: [3](#0-2) 

**Why the balance is not restored:**

In go-ethereum, balance is stored inside the `stateObject` struct and every change is tracked by a `balanceChange` journal entry whose `revert` calls `obj.setBalance(ch.prev)`. Ethermint stores balance in the Cosmos bank module instead. Bank writes go through `s.ctx`, which carries the branched `cacheMS`: [4](#0-3) 

`RevertToSnapshot` only replays journal entries; it does **not** roll back `cacheMS`: [5](#0-4) 

There is no `balanceChange` journal entry in Ethermint's journal type list: [6](#0-5) 

The `nativeChange` mechanism (which *can* restore store state) is only appended by `ExecuteNativeAction`, not by `SubBalance` / `AddBalance`. Therefore the bank-module write made by `SubBalance` inside `SelfDestruct` survives any subsequent `RevertToSnapshot` call.

**Commit-time comment confirms the immediate burn:**

The `Commit()` function's own comment confirms that `SelfDestruct` already burned the balance at call time, and `Commit()` only handles *post-destruction* additions: [7](#0-6) 

---

### Impact Explanation

When a sub-call containing `SELFDESTRUCT` is later reverted by the EVM (e.g., the outer call issues `REVERT` or runs out of gas):

- `selfDestructChange.Revert` restores `selfDestructed =

### Citations

**File:** x/evm/statedb/statedb.go (L123-151)
```go
func NewWithParams(ctx sdk.Context, keeper Keeper, txConfig TxConfig, evmDenom string) *StateDB {
	// Branch the parent multistore. In unit tests the multistore may be uncached, so fall back to CacheWrap.
	var branched any
	if parentCacheMS, ok := ctx.MultiStore().(cachemulti.Store); ok {
		branched = parentCacheMS.CacheMultiStore()
	} else {
		branched = ctx.MultiStore().CacheWrap()
	}
	cacheMS, ok := branched.(cachemulti.Store)
	if !ok {
		panic("expect branched multistore to be cachemulti.Store")
	}
	db := &StateDB{
		origCtx:          ctx,
		keeper:           keeper,
		cacheMS:          cacheMS,
		cacheLayers:      []cachemulti.Store{cacheMS},
		stateObjects:     make(map[common.Address]*stateObject),
		journal:          newJournal(),
		accessList:       newAccessList(),
		transientStorage: newTransientStorage(),

		txConfig: txConfig,

		nativeEvents: sdk.Events{},
		evmDenom:     evmDenom,
	}
	db.ctx = ctx.WithValue(StateDBContextKey, db).WithMultiStore(cacheMS)
	return db
```

**File:** x/evm/statedb/statedb.go (L557-576)
```go
func (s *StateDB) SelfDestruct(addr common.Address) uint256.Int {
	stateObject := s.getStateObject(addr)
	var prevBalance uint256.Int
	if stateObject == nil {
		return prevBalance
	}
	prevBalance = *(stateObject.Balance())
	s.journal.append(selfDestructChange{
		account:     &addr,
		prev:        stateObject.selfDestructed,
		prevbalance: new(uint256.Int).Set(&prevBalance),
	})
	stateObject.markSelfDestructed()
	// clear balance
	balance := s.GetBalance(addr)
	if balance.Sign() > 0 {
		s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
	}
	return prevBalance
}
```

**File:** x/evm/statedb/statedb.go (L719-733)
```go
// RevertToSnapshot reverts all state changes made since the given revision.
func (s *StateDB) RevertToSnapshot(revid int) {
	// Find the snapshot in the stack of valid snapshots.
	idx := sort.Search(len(s.validRevisions), func(i int) bool {
		return s.validRevisions[i].id >= revid
	})
	if idx == len(s.validRevisions) || s.validRevisions[idx].id != revid {
		panic(fmt.Errorf("revision id %v cannot be reverted", revid))
	}
	snapshot := s.validRevisions[idx].journalIndex

	// Replay the journal to undo changes and remove invalidated snapshots
	s.journal.Revert(s, snapshot)
	s.validRevisions = s.validRevisions[:idx]
}
```

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/statedb/journal.go (L96-148)
```go
type (
	// Changes to the account trie.
	createObjectChange struct {
		account *common.Address
	}
	// createContractChange represents an account becoming a contract-account.
	// This event happens prior to executing initcode. The journal-event simply
	// manages the created-flag, in order to allow same-tx destruction.
	createContractChange struct {
		account *common.Address
	}
	resetObjectChange struct {
		prev *stateObject
	}
	selfDestructChange struct {
		account     *common.Address
		prev        bool // whether account had already self-destructed
		prevbalance *uint256.Int
	}

	nonceChange struct {
		account *common.Address
		prev    uint64
	}
	storageChange struct {
		account       *common.Address
		key, prevalue common.Hash
	}
	codeChange struct {
		account            *common.Address
		prevcode, prevhash []byte
	}

	// Changes to other state values.
	refundChange struct {
		prev uint64
	}
	addLogChange struct{}

	// Changes to the access list
	accessListAddAccountChange struct {
		address *common.Address
	}
	accessListAddSlotChange struct {
		address *common.Address
		slot    *common.Hash
	}

	transientStorageChange struct {
		account       *common.Address
		key, prevalue common.Hash
	}
)
```

**File:** x/evm/statedb/journal.go (L188-193)
```go
func (ch selfDestructChange) Revert(s *StateDB) {
	obj := s.getStateObject(*ch.account)
	if obj != nil {
		obj.selfDestructed = ch.prev
	}
}
```
