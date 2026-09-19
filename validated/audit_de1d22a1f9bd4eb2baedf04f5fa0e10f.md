### Title
OCC write barrier gap: `Finalise` clears self-destructed storage/code/nonce without marking `stateAccessStorage`/`stateAccessCode`/`stateAccessNonce` writes - (File: `giga/evmonly/state_db.go`)

### Summary
The Motoko GC advisory describes uninitialized/incorrect memory access caused by missing or incorrect write-barrier instrumentation in a subset of mutation paths, letting the collector (or here, the conflict tracker) miss a mutation and later serve/accept stale or inconsistent data. `sei-chain`'s `giga/evmonly` Block-STM style OCC executor implements the analogous mechanism: every state mutation on `nativeStateDB` must call `markWrite(stateAccessKey{...})` so the OCC validator (`giga/evmonly/occ.go`) can detect conflicts between speculative incarnations. Several mutation sites correctly pair the write with `markWrite`, e.g. `SetState` [1](#0-0) , `SetBalance`/`SetNonce`/`SetCode` [2](#0-1) , and `SelfDestruct` itself [3](#0-2) .

However, `Finalise`, which runs once per transaction to apply deferred effects of self-destructed and newly-created accounts, mutates `Storage`, `StorageCleared`, `Nonce`, `Code`, and `CodeHash` directly and only calls the journal recorders `recordAccount`/`recordStorageMap` (for snapshot revert) — it never calls `markWrite` for these fields: [4](#0-3) 

Contrast this with every other place that mutates `Storage`/`Nonce`/`Code`, which pairs the mutation with an explicit `markWrite(stateAccessKey{...})` call (`SetState` line 435, `SetNonce` line 359, `SetCode` line 389, `SetStorage` line 445). `Finalise`'s bulk storage wipe (`acct.Storage = map[common.Hash]storageValue{}`, `acct.Nonce = 0`, `acct.Code = nil`) is missing the equivalent `markWrite(stateAccessKey{kind: stateAccessStorage, ...})` / `stateAccessNonce` / `stateAccessCode` calls for every slot/field it touches.

### Finding Description
`markWrite`/`markRead` populate `s.writeSet`/`s.readSet`, which are later captured via `accessSets()` [5](#0-4)  and fed into `stateAccessIndex.addAllAt` for OCC conflict tracking [6](#0-5) . Per the documented design, "Validation then walks transaction order, comparing each incarnation's recorded balance, nonce, code, account, and `(address, slot)` storage reads/writes against writes accepted after that incarnation's source prefix" [7](#0-6) . The design explicitly notes that account-level writes do not automatically imply storage-slot writes: `addAllAt` only escalates a write to the coarser "touched"/"account" indices when `key.kind != stateAccessStorage` or `key.kind == stateAccessAccount` respectively, but a genuine per-slot conflict is only detected if the specific `stateAccessStorage{address, slot}` key is present in the write set [6](#0-5) .

Because `Finalise`'s self-destruct storage wipe, nonce reset, and code clear never populate `writeSet` with `stateAccessStorage`/`stateAccessNonce`/`stateAccessCode` keys for the affected address, a concurrently-speculated later transaction (run against the pre-finalise base state) that reads a specific storage slot, nonce, or code of that same address will not see a write-write/read-write conflict for those keys at OCC validation time. The `stateAccessAccount` key is marked by the earlier `SelfDestruct()` call itself, so an *account*-scoped reader (`Exist`/`Empty`) is protected, but any reader that only reads `stateAccessStorage`/`stateAccessNonce`/`stateAccessCode` for that address (the common case for a contract call reading a specific slot, or an EOA nonce check) can be validated as non-conflicting even though the value it observed will differ once the earlier transaction's `Finalise` wipe is applied to the accepted prefix.

This is the direct sei-chain analog of the Motoko bug class: a subset of mutation paths ("a few locations") bypass the barrier that the concurrency/collection system depends on for correctness, producing state that is inconsistent with what the tracker believes was read/written.

### Impact Explanation
If a later, concurrently-executed transaction observes stale storage/nonce/code for a self-destructing contract's address (because the finalise-time wipe wasn't recorded as a conflicting write), the OCC validator can incorrectly accept that transaction's incarnation into the canonical prefix. This can let a transaction execute against data that will no longer be true after the self-destructed account is actually cleared (e.g., reading a storage slot value that should have already been zeroed, or a nonce that should have been reset), leading to incorrect execution results being committed to the chain — a form of unauthorized/incorrect state transition reachable purely from ordinary EVM transactions (a contract using `SELFDESTRUCT` followed by another transaction in the same block touching the same address). Because `evmonly` is the deferred/parallel EVM execution path feeding block results, an accepted-but-incorrect incarnation directly corrupts committed state (balances/storage), which is a fund-loss/state-corruption class issue, matching the CVSS "C:L/I:L/A:L"-style profile of the original advisory.

### Likelihood Explanation
Exploiting this requires: (1) `OCCWorkers > 1` and more than one transaction in the block so the Block-STM path is taken, (2) a transaction that self-destructs a contract, and (3) a subsequent transaction in the same block that reads that contract's storage/nonce/code before the self-destructing transaction's `Finalise` writes are incorporated in program order. All three conditions are achievable by an ordinary contract deployer/caller crafting two transactions in one block — no privileged access, node compromise, or governance is required. This matches the "AC:H" characterization of the original bug (specific ordering/incarnation conditions must line up) while remaining reachable by any unprivileged sender.

### Recommendation
In `Finalise`, add explicit `markWrite` calls for every field the self-destruct/created cleanup path mutates: `stateAccessKey{kind: stateAccessStorage, address: addr, slot: <each cleared slot>}` for every slot present in the pre-clear `acct.Storage` (or a documented account-wide storage write marker consumed by validation), plus `stateAccessKey{kind: stateAccessNonce, address: addr}` and `stateAccessKey{kind: stateAccessCode, address: addr}`, mirroring the barrier pattern already used in `SetState`/`SetNonce`/`SetCode`/`SetStorage`. Add a regression test analogous to `TestStateDBSelfDestructMarksBalanceWrite` [8](#0-7)  but asserting that after `Finalise(true)`, the write set also contains `stateAccessStorage`/`stateAccessNonce`/`stateAccessCode` entries for the self-destructed address, and add an OCC-level conflict test (similar to `TestValidateSTMConflictMatrix`) exercising a self-destruct transaction followed by a reader of that address's storage/nonce/code in the same block.

### Proof of Concept
1. Enable OCC (`OCCWorkers > 1`) and construct a block with two transactions touching the same contract address `A`:
   - Tx1: calls a function on `A` that executes `SELFDESTRUCT`.
   - Tx2: reads a specific storage slot of `A` (e.g., `SLOAD`) or `A`'s nonce/code, and branches its logic on the value.
2. Under Block-STM, Tx2's initial incarnation runs speculatively against base state (before Tx1's finalise wipe is applied), observing the pre-destruct storage/nonce/code value(s).
3. `SelfDestruct(A)` marks only `stateAccessAccount`/`stateAccessBalance` as writes; the follow-up `Finalise` call performs the actual storage/nonce/code wipe but records no `markWrite` for `stateAccessStorage`/`stateAccessNonce`/`stateAccessCode` on `A`.
4. During validation, `stateAccessIndex.addAllAt`/`hasWriteAtOrAfter` never see a write at those specific `(address, slot)`/`nonce`/`code` keys for Tx1, so Tx2's read set for those keys is not flagged as conflicting even though Tx1's finalise wipe changed them.
5. Tx2's stale-read incarnation is accepted into the block's canonical output, producing a state transition inconsistent with sequential (correct) execution — demonstrable via a unit test using `newNativeStateDB`, driving `SelfDestruct` + `Finalise`, and inspecting `accessSets()` against a manually constructed reader incarnation as in `TestStateDBGetCodeHashTracksCodelessAccountExistenceReads` [9](#0-8) .

### Citations

**File:** giga/evmonly/state_db.go (L340-396)
```go
func (s *nativeStateDB) SetBalance(addr common.Address, balance *uint256.Int, _ tracing.BalanceChangeReason) {
	acct := s.account(addr)
	s.recordAccount(addr)
	s.markWrite(stateAccessKey{kind: stateAccessBalance, address: addr})
	if balance == nil {
		acct.Balance = uint256.NewInt(0)
		return
	}
	acct.Balance = balance.Clone()
}

func (s *nativeStateDB) GetNonce(addr common.Address) uint64 {
	s.markRead(stateAccessKey{kind: stateAccessNonce, address: addr})
	return s.account(addr).Nonce
}

func (s *nativeStateDB) SetNonce(addr common.Address, nonce uint64, _ tracing.NonceChangeReason) {
	acct := s.account(addr)
	s.recordAccount(addr)
	s.markWrite(stateAccessKey{kind: stateAccessNonce, address: addr})
	acct.Nonce = nonce
}

func (s *nativeStateDB) GetCodeHash(addr common.Address) common.Hash {
	s.markRead(stateAccessKey{kind: stateAccessCode, address: addr})
	acct := s.account(addr)
	if len(acct.Code) > 0 {
		if acct.CodeHash == (common.Hash{}) {
			acct.CodeHash = crypto.Keccak256Hash(acct.Code)
		}
		return acct.CodeHash
	}
	s.markRead(stateAccessKey{kind: stateAccessBalance, address: addr})
	s.markRead(stateAccessKey{kind: stateAccessNonce, address: addr})
	if acct.Nonce == 0 && acct.Balance.IsZero() {
		return common.Hash{}
	}
	return ethtypes.EmptyCodeHash
}

func (s *nativeStateDB) GetCode(addr common.Address) []byte {
	s.markRead(stateAccessKey{kind: stateAccessCode, address: addr})
	return s.account(addr).Code
}

func (s *nativeStateDB) SetCode(addr common.Address, code []byte) []byte {
	acct := s.account(addr)
	prev := cloneBytes(acct.Code)
	s.recordAccount(addr)
	s.markWrite(stateAccessKey{kind: stateAccessCode, address: addr})
	acct.Code = cloneBytes(code)
	acct.CodeHash = common.Hash{}
	if len(acct.Code) != 0 {
		acct.CodeHash = crypto.Keccak256Hash(acct.Code)
	}
	return prev
}
```

**File:** giga/evmonly/state_db.go (L429-439)
```go
func (s *nativeStateDB) SetState(addr common.Address, key common.Hash, value common.Hash) common.Hash {
	s.ensureStorage(addr, key)
	acct := s.account(addr)
	prev := storageHash(acct.Storage, key)
	s.recordAccount(addr)
	s.recordStorage(addr, key)
	s.markWrite(stateAccessKey{kind: stateAccessStorage, address: addr, slot: key})
	acct.Storage[key] = storageValue{value: value}
	s.markTxStorageWrite(addr, key)
	return prev
}
```

**File:** giga/evmonly/state_db.go (L486-496)
```go
func (s *nativeStateDB) SelfDestruct(addr common.Address) uint256.Int {
	acct := s.account(addr)
	prev := *acct.Balance.Clone()
	s.recordAccount(addr)
	s.markWrite(stateAccessKey{kind: stateAccessAccount, address: addr})
	s.markWrite(stateAccessKey{kind: stateAccessBalance, address: addr})
	acct.Balance.Clear()
	acct.SelfDestructed = true
	s.markForFinalise(addr)
	return prev
}
```

**File:** giga/evmonly/state_db.go (L636-658)
```go
func (s *nativeStateDB) Finalise(bool) {
	for addr := range s.finaliseAddrs {
		acct := s.account(addr)
		if acct.SelfDestructed {
			s.recordAccount(addr)
			s.recordStorageMap(addr)
			acct.Code = nil
			acct.CodeHash = common.Hash{}
			acct.Storage = map[common.Hash]storageValue{}
			acct.StorageCleared = true
			acct.Nonce = 0
			acct.SelfDestructed = false
			s.markTxStorageClear(addr)
		}
		if acct.Created {
			s.recordAccount(addr)
			acct.Created = false
		}
	}
	s.finaliseTxStorage()
	clear(s.finaliseAddrs)
	s.refund = 0
}
```

**File:** giga/evmonly/state_db.go (L744-746)
```go
func (s *nativeStateDB) accessSets() (map[stateAccessKey]struct{}, map[stateAccessKey]struct{}) {
	return cloneAccessSet(s.readSet), cloneAccessSet(s.writeSet)
}
```

**File:** giga/evmonly/occ.go (L783-794)
```go
func (i *stateAccessIndex) addAllAt(txIndex int, set map[stateAccessKey]struct{}) {
	for key := range set {
		i.recordWrite(i.exact, key, txIndex)
		// Exist/Empty account reads depend on account metadata, not storage slots.
		if key.kind != stateAccessStorage {
			i.recordAddressWrite(i.touched, key.address, txIndex)
		}
		if key.kind == stateAccessAccount {
			i.recordAddressWrite(i.account, key.address, txIndex)
		}
	}
}
```

**File:** giga/evmonly/README.md (L202-205)
```markdown
down to one transaction per range. Validation then walks transaction order,
comparing each incarnation's recorded balance, nonce, code, account, and
`(address, slot)` storage reads/writes against writes accepted after that
incarnation's source prefix.
```

**File:** giga/evmonly/executor_test.go (L1642-1652)
```go
func TestStateDBSelfDestructMarksBalanceWrite(t *testing.T) {
	contract := testAddress(0xc3)
	stateDB := newNativeStateDB(NewMemoryState())
	stateDB.enableAccessTracking()

	stateDB.SelfDestruct(contract)

	_, writes := stateDB.accessSets()
	require.Contains(t, writes, stateAccessKey{kind: stateAccessAccount, address: contract})
	require.Contains(t, writes, stateAccessKey{kind: stateAccessBalance, address: contract})
}
```

**File:** giga/evmonly/executor_test.go (L2121-2142)
```go
func TestStateDBGetCodeHashTracksCodelessAccountExistenceReads(t *testing.T) {
	eoa := testAddress(0xbd)
	state := NewMemoryState()
	state.SetBalance(eoa, big.NewInt(1))
	stateDB := newNativeStateDB(state)
	stateDB.enableAccessTracking()

	require.Equal(t, ethtypes.EmptyCodeHash, stateDB.GetCodeHash(eoa))
	readSet, _ := stateDB.accessSets()
	require.Contains(t, readSet, stateAccessKey{kind: stateAccessCode, address: eoa})
	require.Contains(t, readSet, stateAccessKey{kind: stateAccessBalance, address: eoa})
	require.Contains(t, readSet, stateAccessKey{kind: stateAccessNonce, address: eoa})

	writes := newStateAccessIndex()
	writes.addAll(map[stateAccessKey]struct{}{
		{kind: stateAccessBalance, address: eoa}: {},
	})
	validation := occValidationResult{}
	accepted := validateSTMResultAgainstPrefix(&validation, writes, occTxExecution{gasLimit: 1, readSet: readSet}, 0, 10, 0)
	require.False(t, accepted)
	require.Equal(t, occFallbackReasonConflict, validation.fallbackReason)
}
```
