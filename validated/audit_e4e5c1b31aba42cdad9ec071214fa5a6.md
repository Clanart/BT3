Looking at the `SELFDESTRUCT` handling in Ethermint's statedb layer, I need to trace the full revert path for a self-destructed account.

**Key files examined:**
- `x/evm/statedb/statedb.go` — `SelfDestruct`, `RevertToSnapshot`, `Commit`
- `x/evm/statedb/journal.go` — `selfDestructChange.Revert`
- `x/evm/keeper/statedb.go` — `SubBalance`, `DeleteAccount`

---

### Title
Reverted `SELFDESTRUCT` Permanently Burns Contract Balance Due to Missing Balance Restoration in `selfDestructChange.Revert` — (File: `x/evm/statedb/journal.go`, `x/evm/statedb/statedb.go`)

---

### Summary

When `SelfDestruct` is called on a contract, it immediately burns the contract's balance via `SubBalance` through `ExecuteNativeAction`, which writes to the Cosmos bank module through a native cache layer. The `selfDestructChange` journal entry stores `prevbalance` but its `Revert` method only restores the `selfDestructed` flag — it never restores the burned balance. Because `RevertToSnapshot` only replays journal entries and does not revert native cache layer writes, a reverted `SELFDESTRUCT` sub-call permanently destroys the contract's EVM-denom balance, violating the fundamental EVM invariant that reverted operations have no state effect.

---

### Finding Description

**Step 1 — `SelfDestruct` burns balance via native action:**

`SelfDestruct` in `statedb.go` appends a `selfDestructChange` journal entry (storing `prevbalance`) and then immediately calls `SubBalance` via `ExecuteNativeAction`: [1](#0-0) 

`SubBalance` routes through `ExecuteNativeAction`, which writes to the Cosmos bank module through the native cache layer (`s.cacheMS` / `s.cacheLayers`): [2](#0-1) 

**Step 2 — `selfDestructChange.Revert` does NOT restore the balance:**

The `selfDestructChange` struct stores `prevbalance` but `Revert` only resets the `selfDestructed` flag: [3](#0-2) [4](#0-3) 

The `prevbalance` field is stored but never used in `Revert`. There is no `AddBalance` call to undo the `SubBalance` that was executed during `SelfDestruct`.

**Step 3 — `RevertToSnapshot` only replays journal entries, not native cache layers:**

`RevertToSnapshot` calls only `s.journal.Revert(s, snapshot)`: [5](#0-4) 

It does not call `restoreNativeState` or truncate `s.cacheLayers`. The native bank writes from `SubBalance` (committed into the native cache layer by `ExecuteNativeAction`) are therefore **not undone** when the EVM reverts a sub-call.

**Step 4 — The native cache layer is only flushed at `Commit` time:** [6](#0-5) 

Once `flushNativeCacheLayers()` runs at commit, the burned balance is permanently written to the underlying store — even if the `SELFDESTRUCT` was inside a reverted sub-call.

---

### Impact Explanation

This is a **High** severity EVM state transition accounting bug. Any contract that self-destructs inside a sub-call that is subsequently reverted (e.g., the outer call catches a revert, or the sub-call itself reverts after `SELFDESTRUCT` is executed) will have its entire EVM-denom balance permanently burned. The `selfDestructed` flag is correctly restored to `false` by the journal revert, but the Cosmos bank balance is not restored. The result is a contract that appears alive (non-zero nonce, code, storage) but has zero balance — funds are irrecoverably destroyed without authorization.

This breaks the core EVM invariant: **a reverted operation must have no observable state effect**. Any DeFi protocol relying on this invariant (which is universal) is affected.

---

### Likelihood Explanation

The trigger pattern is reachable by any unprivileged user deploying a contract. A concrete scenario:

1. Attacker deploys a factory contract.
2. Factory deploys a victim contract (funded with EVM-denom).
3. Factory calls victim, which executes `SELFDESTRUCT` targeting the attacker.
4. Factory's outer call reverts (e.g., via `require(false)` after the self-destruct sub-call).
5. The EVM reverts the `selfDestructed` flag but the balance burned by `SubBalance` is not restored.
6. Victim contract's balance is permanently zero; funds are destroyed.

No privileged access, governance, or validator compromise is required. The entry path is a standard EVM transaction.

---

### Recommendation

`selfDestructChange.Revert` must restore the burned balance using the stored `prevbalance`. The fix should call `AddBalance` (via `ExecuteNativeAction`) to credit back the previously burned amount:

```go
func (ch selfDestructChange) Revert(s *StateDB) {
    obj := s.getStateObject(*ch.account)
    if obj != nil {
        obj.selfDestructed = ch.prev
        // Restore the balance that was burned by SelfDestruct.
        if ch.prevbalance != nil && ch.prevbalance.Sign() > 0 {
            s.AddBalance(*ch.account, ch.prevbalance, tracing.BalanceIncreaseRevert)
        }
    }
}
```

This mirrors how go-ethereum handles the same revert: it restores the balance in the journal revert path. The `prevbalance` field is already stored in `selfDestructChange` precisely for this purpose but is currently unused. [3](#0-2) 

---

### Proof of Concept

```
1. Deploy FundedContract with 1 ETH balance.
2. Deploy AttackerFactory.
3. AttackerFactory calls FundedContract.selfDestruct(attacker).
   → StateDB.SelfDestruct() fires:
       - selfDestructChange{prev: false, prevbalance: 1 ETH} appended to journal
       - SubBalance(FundedContract, 1 ETH) executed via ExecuteNativeAction
         → bank balance of FundedContract is now 0 in native cache layer
4. AttackerFactory's outer call reverts (require(false)).
   → RevertToSnapshot() fires:
       - selfDestructChange.Revert() sets selfDestructed = false  ✓
       - SubBalance write in native cache layer is NOT undone      ✗
5. StateDB.Commit() calls flushNativeCacheLayers():
   → The SubBalance write (balance = 0) is flushed to the underlying store.
6. FundedContract.selfDestructed == false (appears alive)
   FundedContract bank balance == 0 (funds permanently destroyed)
```

The `prevbalance` field stored in `selfDestructChange` at line 113 of `journal.go` is never read in `Revert` at lines 188–193, confirming the missing restoration path. [4](#0-3) [7](#0-6)

### Citations

**File:** x/evm/statedb/statedb.go (L418-422)
```go
func (s *StateDB) flushNativeCacheLayers() {
	for i := len(s.cacheLayers) - 1; i >= 0; i-- {
		s.cacheLayers[i].Write()
	}
}
```

**File:** x/evm/statedb/statedb.go (L473-491)
```go
// SubBalance subtracts amount from the account associated with addr.
func (s *StateDB) SubBalance(addr common.Address, amount *uint256.Int, _ tracing.BalanceChangeReason) uint256.Int {
	if amount.Sign() == 0 {
		return uint256.Int{}
	}
	if amount.Sign() < 0 {
		panic("negative amount")
	}
	coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(amount.ToBig()))
	var balance uint256.Int
	if err := s.ExecuteNativeAction(common.Address{}, nil, func(ctx sdk.Context) error {
		var subErr error
		balance, subErr = s.keeper.SubBalance(ctx, sdk.AccAddress(addr.Bytes()), coin)
		return subErr
	}); err != nil {
		s.err = err
	}

	return balance
```

**File:** x/evm/statedb/statedb.go (L557-575)
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

**File:** x/evm/statedb/journal.go (L110-114)
```go
	selfDestructChange struct {
		account     *common.Address
		prev        bool // whether account had already self-destructed
		prevbalance *uint256.Int
	}
```

**File:** x/evm/statedb/journal.go (L188-197)
```go
func (ch selfDestructChange) Revert(s *StateDB) {
	obj := s.getStateObject(*ch.account)
	if obj != nil {
		obj.selfDestructed = ch.prev
	}
}

func (ch selfDestructChange) Dirtied() *common.Address {
	return ch.account
}
```
