### Title
`selfDestructChange.Revert` Ignores Stored `prevbalance`, Permanently Burning Contract Funds on Reverted SELFDESTRUCT - (File: x/evm/statedb/journal.go)

---

### Summary

`selfDestructChange.Revert` in `x/evm/statedb/journal.go` saves the pre-destruction balance in `prevbalance` but never uses it when reverting. Because Ethermint's `StateDB` has no `balanceChange` journal entry and `Snapshot()` does not branch the underlying cache multistore, the `SubBalance` call made inside `SelfDestruct` is irreversible through the journal mechanism. When a sub-call containing `SELFDESTRUCT` is later reverted via `RevertToSnapshot`, the `selfDestructed` flag is correctly reset to `false`, but the burned balance is never restored — permanently destroying the contract's funds.

---

### Finding Description

**Root cause — `SelfDestruct` burns balance, journal entry stores `prevbalance` but `Revert` never uses it:**

`SelfDestruct` in `x/evm/statedb/statedb.go` (lines 557–575):

```go
func (s *StateDB) SelfDestruct(addr common.Address) uint256.Int {
    stateObject := s.getStateObject(addr)
    ...
    prevBalance = *(stateObject.Balance())
    s.journal.append(selfDestructChange{
        account:     &addr,
        prev:        stateObject.selfDestructed,
        prevbalance: new(uint256.Int).Set(&prevBalance),   // ← saved
    })
    stateObject.markSelfDestructed()
    balance := s.GetBalance(addr)
    if balance.Sign() > 0 {
        s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)  // ← burned via bank keeper
    }
    return prevBalance
}
``` [1](#0-0) 

`selfDestructChange.Revert` in `x/evm/statedb/journal.go` (lines 188–193):

```go
func (ch selfDestructChange) Revert(s *StateDB) {
    obj := s.getStateObject(*ch.account)
    if obj != nil {
        obj.selfDestructed = ch.prev   // ← only the flag is restored
        // prevbalance is NEVER used — balance is not restored
    }
}
``` [2](#0-1) 

The `selfDestructChange` struct explicitly carries `prevbalance *uint256.Int`: [3](#0-2) 

**Why no other mechanism restores the balance:**

`Snapshot()` records only the journal length — it does not branch the cache multistore: [4](#0-3) 

`RevertToSnapshot` only replays journal entries; it never touches `s.ctx` or `s.cacheMS`: [5](#0-4) 

There is no `balanceChange` journal entry anywhere in `journal.go` — the full set of journal entry types contains no balance-restoration entry: [6](#0-5) 

`SubBalance` inside `SelfDestruct` calls the bank keeper through `s.ctx`, which is a branched context that is never rolled back by the journal mechanism. The burn is therefore permanent once `SubBalance` executes, regardless of any subsequent `RevertToSnapshot`.

---

### Impact Explanation

When a contract executes `SELFDESTRUCT` inside a sub-call that is later reverted (a standard EVM pattern — e.g., a factory contract that creates-and-destructs, or any call chain where an outer frame reverts), the following happens:

1. `SelfDestruct` burns the contract's entire EVM-denom balance via the bank keeper.
2. `RevertToSnapshot` is called by the EVM to undo the sub-call.
3. `selfDestructChange.Revert` resets `selfDestructed = false` — the account appears alive again.
4. The burned balance is **never restored** — `prevbalance` is silently discarded.

The contract continues to exist (flag reset) but with zero balance. The funds are permanently destroyed from the Cosmos bank, constituting unauthorized burn of EVM-denom funds through Ethermint's stateDB logic.

This matches: **Critical — Unauthorized burn of EVM-denom or Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic.**

---

### Likelihood Explanation

Any unprivileged user can trigger this by submitting an Ethereum transaction that causes `SELFDESTRUCT` to execute inside a sub-call that subsequently reverts. This is a standard EVM pattern (e.g., `CREATE2` + `SELFDESTRUCT` + outer revert, or any contract that conditionally self-destructs). No special privileges, governance access, or validator collusion is required. The entry path is a normal `eth_sendRawTransaction` call.

---

### Recommendation

`selfDestructChange.Revert` must restore the balance using the stored `prevbalance`. Since Ethermint manages balances through the bank keeper rather than in-memory, the revert must call `AddBalance` to re-mint the burned tokens:

```go
func (ch selfDestructChange) Revert(s *StateDB) {
    obj := s.getStateObject(*ch.account)
    if obj != nil {
        obj.selfDestructed = ch.prev
        if ch.prevbalance.Sign() > 0 {
            s.AddBalance(*ch.account, ch.prevbalance, tracing.BalanceIncreaseSelfdestructRevert)
        }
    }
}
```

Alternatively, align with go-ethereum's approach by journaling balance changes separately via a `balanceChange` entry so that all `SubBalance`/`AddBalance` calls made during `SelfDestruct` (and elsewhere) are automatically reverted by the journal.

---

### Proof of Concept

```
1. Deploy ContractA with 1 ETH balance.
2. Deploy ContractB whose constructor calls SELFDESTRUCT(beneficiary).
3. Deploy ContractFactory with logic:
      function attack() external {
          try new ContractB{value: 0}() {} catch {}
          // outer call continues; sub-call (ContractB creation) was reverted
      }
4. Call ContractA.attack() which internally:
      a. Takes a snapshot (EVM sub-call boundary).
      b. Calls SELFDESTRUCT on ContractA inside the sub-call.
      c. The sub-call reverts (outer frame catches the revert).
5. Observe: ContractA.selfDestructed == false (flag correctly reset),
            but ContractA balance == 0 (funds permanently burned).
6. The 1 ETH is gone from the Cosmos bank with no corresponding credit anywhere.
```

The `prevbalance` field in the journal entry holds the correct pre-destruction balance but is never passed to `AddBalance` during revert, directly mirroring the original report's pattern of a saved value being discarded instead of used for fund restoration.

### Citations

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

**File:** x/evm/statedb/statedb.go (L712-717)
```go
func (s *StateDB) Snapshot() int {
	id := s.nextRevisionID
	s.nextRevisionID++
	s.validRevisions = append(s.validRevisions, revision{id, s.journal.length()})
	return id
}
```

**File:** x/evm/statedb/statedb.go (L720-733)
```go
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
