[File: 'x/evm/statedb/journal.go'] [Function: journal.append / Dirtied() accounting (journal.go:67-72)] Can an unprivileged attacker cause an existing account to not be reset in the keeper after CreateAccount overwrites it, by exploiting the fact that resetObjectChange.Dirtied()=nil means the address is not added to journal.dirties, under the precondition that Commit() iterates journal.sortedDirties() and only commits accounts in that set, by executing: (1) existing account at addr with nonce=5, code=X, (2) CreateAccount(addr) appends resetObjectChange (Dirtied=nil, prev=existingObject), (3) no further modifications to addr (no nonceChange, codeChange, storageChange), (4) Commit(): journal.sortedDir

### Citations

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

**File:** x/evm/statedb/native.go (L10-23)
```go
type nativeChange struct {
	previousStore      cachemulti.Store
	previousLayerCount int
	events             int
}

func (native nativeChange) Dirtied() *common.Address {
	return nil
}

func (native nativeChange) Revert(s *StateDB) {
	s.restoreNativeState(native.previousStore, native.previousLayerCount)
	s.nativeEvents = s.nativeEvents[:len(s.nativeEvents)-native.events]
}
```

**File:** x/evm/statedb/statedb.go (L370-422)
```go
// ExecuteNativeAction executes native action in isolate,
// the writes will be revert when either the native action itself fail
// or the wrapping message call reverted.
func (s *StateDB) ExecuteNativeAction(contract common.Address, converter EventConverter, action func(ctx sdk.Context) error) error {
	prevStore := s.cacheMS
	prevLayerCount := len(s.cacheLayers)

	nextStore, ok := s.cacheMS.CacheMultiStore().(cachemulti.Store)
	if !ok {
		panic(
