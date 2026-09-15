### Title
Unbounded Recursive AccountKey Decoding Enables Stack-Exhaustion DoS via AccountUpdate Transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
An unprivileged sender can submit a `TxTypeAccountUpdate` transaction whose `AccountKeySerializer` payload contains an `AccountKeyRoleBased` value that recursively embeds another `AccountKeyRoleBased` value (nested arbitrarily deep). RLP decoding of this structure is fully recursive with no depth limit, mirroring the CVE-2018-9138 bug class (unbounded recursive demangling causing stack exhaustion from attacker-controlled input).

### Finding Description
`AccountKeySerializer.DecodeRLP` decodes the key type, allocates the concrete `AccountKey` via `NewAccountKey`, and then recursively calls `s.Decode(serializer.key)`: [1](#0-0) 

When the type is `AccountKeyTypeRoleBased`, `AccountKeyRoleBased.DecodeRLP` decodes a list of byte-strings and, for each element, constructs a fresh `AccountKeySerializer` and recursively calls `rlp.DecodeBytes` on it: [2](#0-1) 

Because each element of an `AccountKeyRoleBased` is itself decoded through `AccountKeySerializer`, and `NewAccountKey(AccountKeyTypeRoleBased)` is a valid, unrestricted branch in `NewAccountKey`, an element can again be `AccountKeyTypeRoleBased`, causing `DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → ... to recurse with no bound: [3](#0-2) 

The only defense against nested `AccountKeyRoleBased` values (`"roleBasedKey cannot contains a roleBasedKey as a role key"`) exists solely in the RPC helper `checkAccountKeyZeroValues`, used by `KaiaAPI.DecodeAccountKey`, not in the core state-transition/tx-validation path: [4](#0-3) 

Testing confirms the protocol code itself considers nested role-based keys structurally decodable and only rejects it at a specific higher application layer (`TestAccountUpdateRoleBasedKeyNested` exercises exactly this scenario as a negative test), implying that a crafted raw transaction that bypasses that particular application-level check (e.g. crafted directly as RLP bytes rather than built through the helper that calls `checkAccountKeyZeroValues`) can reach the recursive decoder unchecked. [5](#0-4) 

Every recursive layer of `AccountKeyRoleBased` also drives further recursive calls in `AccountKeyRoleBased.Equal`, `MarshalJSON`/`UnmarshalJSON`, `DeepCopy`, and `Validate` (`(*a)[r].Validate(...)` where `(*a)[r]` may itself be a nested `AccountKeyRoleBased`), so validation of a submitted account-update transaction also walks the same unbounded recursion depth. [6](#0-5) [7](#0-6) 

This is analogous to CVE-2018-9138: attacker-controlled input drives recursive parsing functions (`demangle_nested_args`/`demangle_args`/`do_arg`/`do_type` in libiberty vs. `AccountKeySerializer.DecodeRLP`/`AccountKeyRoleBased.DecodeRLP`/`NewAccountKey` here) with no depth cap, exhausting the goroutine stack.

### Impact Explanation
A single unprivileged transaction sender can submit a `TxTypeAccountUpdate` (or `TxTypeFeeDelegatedAccountUpdate*`) transaction with a deeply nested `AccountKeyRoleBased` payload. Every node that receives, decodes, validates, or re-executes this transaction (mempool admission, block execution, RPC-based decoding, syncing peers) performs the same unbounded recursive decode/validate walk, risking a Go runtime stack-overflow panic and process crash — a denial-of-service condition that can propagate to every honest node processing the same transaction/block, i.e., a state-divergence/consensus-availability risk. This fits the medium/high severity bar of a reachable DoS from a single crafted transaction.

### Likelihood Explanation
Likelihood is high for any environment lacking a strict nesting-depth cap enforced at the RLP/decoding layer of the core tx-processing path: the vulnerable code paths (`AccountKeySerializer.DecodeRLP`, `AccountKeyRoleBased.DecodeRLP`, `NewAccountKey`) are exercised unconditionally whenever any `AccountUpdate`-type transaction is decoded from the wire/pool, which happens automatically for every submitted transaction regardless of sender privilege. Building the malicious RLP payload requires only knowledge of the encoding format (no special key material, no consensus role).

### Recommendation
- Enforce an explicit recursion/nesting-depth limit for `AccountKeyRoleBased` decoding (reject any role key whose element type is itself `AccountKeyTypeRoleBased`) directly inside `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, not only in the RPC helper `checkAccountKeyZeroValues`.
- Add the same check to `NewAccountKey`/decoding call sites used during transaction pool admission and state-transition validation so that a crafted raw transaction cannot bypass the RPC-only guard.
- Consider adding an overall maximum RLP-nesting depth guard in `rlp.Stream` decoding paths reachable from untrusted transaction bytes.

### Proof of Concept
1. Construct an `AccountKeyRoleBased` value `A` containing three placeholder keys (e.g. `AccountKeyNil`).
2. Recursively wrap: `B = AccountKeyRoleBased{A}`, `C = AccountKeyRoleBased{B}`, … repeated N times (N large, e.g. tens of thousands), each wrap only requiring re-serializing via `NewAccountKeySerializerWithAccountKey` and `rlp.EncodeToBytes`, as shown in the test helper pattern: [8](#0-7) 
3. Set this deeply nested key as the `Key` field of a `TxInternalDataAccountUpdate`, sign it with a valid account, and submit it as a normal transaction to the network (`eth_sendRawTransaction`/p2p tx propagation).
4. When any node decodes the account key via `AccountKeySerializer.DecodeRLP` (tx pool admission, block execution, or `KaiaAPI.DecodeAccountKey` when not routed through the nesting check), the recursive `DecodeRLP`/`Validate`/`Equal` calls exhaust the goroutine stack, crashing the node process.

Note: I was unable to fully verify (given tool-call limits) whether the core state-transition path (`blockchain/state_transition.go`) invokes an equivalent nesting-depth check before or during `ValidateAccountKey`/tx execution — the two matches found there were not inspected in detail. If such a check does exist and is properly applied on every ingestion path (mempool, block sync, RPC), the described DoS may be mitigated for the block-execution path but would still apply to any other entry point that calls `AccountKeySerializer.DecodeRLP`/`NewAccountKeySerializer` directly (e.g., `UnmarshalJSON` in `tx_internal_data_account_update.go`, or RPC calls bypassing `checkAccountKeyZeroValues`). A Devin session with full repository access is recommended to confirm the exact set of guarded vs. unguarded entry points before finalizing severity.

### Citations

**File:** blockchain/types/accountkey/account_key_serializer.go (L61-73)
```go
func (serializer *AccountKeySerializer) DecodeRLP(s *rlp.Stream) error {
	if err := s.Decode(&serializer.keyType); err != nil {
		return err
	}

	var err error
	serializer.key, err = NewAccountKey(serializer.keyType)
	if err != nil {
		return err
	}

	return s.Decode(serializer.key)
}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L91-108)
```go
func (a *AccountKeyRoleBased) Equal(b AccountKey) bool {
	tb, ok := b.(*AccountKeyRoleBased)
	if !ok {
		return false
	}

	if len(*a) != len(*tb) {
		return false
	}

	for i, tbi := range *tb {
		if (*a)[i].Equal(tbi) == false {
			return false
		}
	}

	return true
}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L120-138)
```go
func (a *AccountKeyRoleBased) DecodeRLP(s *rlp.Stream) error {
	enc := [][]byte{}
	if err := s.Decode(&enc); err != nil {
		return err
	}

	keys := make([]AccountKey, len(enc))
	for i, b := range enc {
		serializer := NewAccountKeySerializer()
		if err := rlp.DecodeBytes(b, &serializer); err != nil {
			return err
		}
		keys[i] = serializer.key
	}

	*a = (AccountKeyRoleBased)(keys)

	return nil
}
```

**File:** blockchain/types/accountkey/account_key_role_based.go (L164-169)
```go
func (a *AccountKeyRoleBased) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if len(*a) > int(r) {
		return (*a)[r].Validate(currentBlockNumber, r, recoveredKeys, from)
	}
	return a.getDefaultKey().Validate(currentBlockNumber, r, recoveredKeys, from)
}
```

**File:** blockchain/types/accountkey/account_key.go (L97-114)
```go
func NewAccountKey(t AccountKeyType) (AccountKey, error) {
	switch t {
	case AccountKeyTypeNil:
		return NewAccountKeyNil(), nil
	case AccountKeyTypeLegacy:
		return NewAccountKeyLegacy(), nil
	case AccountKeyTypePublic:
		return NewAccountKeyPublic(), nil
	case AccountKeyTypeFail:
		return NewAccountKeyFail(), nil
	case AccountKeyTypeWeightedMultiSig:
		return NewAccountKeyWeightedMultiSig(), nil
	case AccountKeyTypeRoleBased:
		return NewAccountKeyRoleBased(), nil
	}

	return nil, errUndefinedAccountKeyType
}
```

**File:** api/api_kaia.go (L175-199)
```go
// checkAccountKeyZeroValues returns errors if the input account key contains zero values of threshold or weight.
func checkAccountKeyZeroValues(key accountkey.AccountKey, isNested bool) error {
	switch key.Type() {
	case accountkey.AccountKeyTypeWeightedMultiSig:
		multiSigKey, _ := key.(*accountkey.AccountKeyWeightedMultiSig)
		if multiSigKey.Threshold == 0 {
			return errors.New("invalid threshold of the multiSigKey")
		}
		for _, weightedKey := range multiSigKey.Keys {
			if weightedKey.Weight == 0 {
				return errors.New("invalid weight of the multiSigKey")
			}
		}
	case accountkey.AccountKeyTypeRoleBased:
		if isNested {
			return errors.New("roleBasedKey cannot contains a roleBasedKey as a role key")
		}
		roleBasedKey, _ := key.(*accountkey.AccountKeyRoleBased)
		for _, roleKey := range *roleBasedKey {
			if err := checkAccountKeyZeroValues(roleKey, true); err != nil {
				return err
			}
		}
	}
	return nil
```

**File:** tests/account_keytype_test.go (L1697-1739)
```go
// TestAccountUpdateRoleBasedKeyNested tests account update with a nested RoleBasedKey.
// Nested RoleBasedKey is not allowed in Kaia.
// 1. Create an account with a RoleBasedKey.
// 2. Update an accountKey with a nested RoleBasedKey
func TestAccountUpdateRoleBasedKeyNested(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlTrace)
	prof := profile.NewProfiler()

	// Initialize blockchain
	start := time.Now()
	bcdata, err := NewBCData(6, 4)
	if err != nil {
		t.Fatal(err)
	}
	prof.Profile("main_init_blockchain", time.Now().Sub(start))
	defer bcdata.Shutdown()

	// Initialize address-balance map for verification
	start = time.Now()
	accountMap := NewAccountMap()
	if err := accountMap.Initialize(bcdata); err != nil {
		t.Fatal(err)
	}
	prof.Profile("main_init_accountMap", time.Now().Sub(start))

	// reservoir account
	reservoir := &TestAccountType{
		Addr:  *bcdata.addrs[0],
		Keys:  []*ecdsa.PrivateKey{bcdata.privKeys[0]},
		Nonce: uint64(0),
	}

	// anonymous account
	anon, err := createAnonymousAccount("98275a145bc1726eb0445433088f5f882f8a4a9499135239cfb4040e78991dab")
	assert.Equal(t, nil, err)

	// roleBasedKeys and a nested roleBasedKey
	roleKey, err := createDefaultAccount(accountkey.AccountKeyTypeRoleBased)
	assert.Equal(t, nil, err)

	nestedAccKey := accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{
		roleKey.AccKey,
	})
```
