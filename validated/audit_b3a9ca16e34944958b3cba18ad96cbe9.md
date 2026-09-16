## Title
Unbounded recursive RLP decoding of `AccountKeyRoleBased` allows a single crafted AccountUpdate transaction to crash a node via Go stack-overflow DoS - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
The reported `eml_parser` bug is a bug class of "unbounded recursive descent parsing of untrusted, attacker-controlled nested input causes uncontrolled resource consumption / crash" (CWE-1124/CWE-770). Kaia's `AccountKeyRoleBased` RLP decoder has the same structural flaw: it recursively decodes nested account keys with no depth limit, and the "no nesting" rule is enforced only *after* decoding completes, not during decoding.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes an arbitrary list of raw byte-strings and, for every element, immediately RLP-decodes it as another `AccountKeySerializer`, which in turn calls `NewAccountKey(keyType)` and decodes into that key type recursively: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` performs the same pattern generically for any key type, including `AccountKeyTypeRoleBased` again: [2](#0-1) 

Nothing in the decode path bounds how many times an `AccountKeyRoleBased` can embed another `AccountKeyRoleBased`. The only defense — rejecting a `RoleBased` key that contains a nested `RoleBased` key (`kerrors.ErrNestedCompositeType`) — is applied *after* the whole structure has already been fully decoded, either in `checkAccountKeyZeroValues`/`CheckInstallable` or in `AccountKeyRoleBased.CheckInstallable`/`CheckUpdatable`: [3](#0-2) [4](#0-3) 

This is exactly analogous to the `eml_parser` flaw: the recursive parser has no depth guard, and any validation that would reject the pathological structure only runs after the (already-costly/crashing) parse has completed.

The path is reachable from a completely unprivileged transaction sender: an `AccountUpdate` (or `FeeDelegatedAccountUpdate*`) transaction's `Key` field is decoded via `TxInternalDataAccountUpdate.DecodeRLP` → `fromSerializable` → `rlp.DecodeBytes(serialized.Key, serializer)`: [5](#0-4) 

This decode happens whenever the transaction bytes are parsed — e.g. when a peer/RPC submits raw transaction bytes to be added to the tx pool, well before any semantic validation such as `CheckInstallable`/`CheckReplacable` (called from `TxInternalDataAccountUpdate.Validate`) runs: [6](#0-5) 

By nesting `AccountKeyRoleBased` inside itself thousands of times (each level only costs a handful of RLP bytes — an outer list header, a `keyType` byte, and an inner byte-string header), an attacker can build a transaction well within normal transaction size limits that drives the Go call stack of `DecodeRLP`/`NewAccountKey`/`rlp.DecodeBytes` down thousands of recursive frames. Unlike Python's `RecursionError`, Go's default goroutine stack-growth failure ("fatal error: stack overflow") is **not recoverable** via `recover()`/`try-catch`, so it terminates the entire node process rather than just failing to parse one message — making the impact strictly worse than in the original advisory.

### Impact Explanation
A successful trigger crashes the Kaia node process, since Go's stack-overflow fatal error cannot be caught by `recover()`. Any component that RLP-decodes an `AccountUpdate`-family transaction from untrusted input (tx-pool ingestion from p2p broadcast, `eth_sendRawTransaction`/`SendTransaction` RPC handling before signature/state validation, or block-body reconstruction) is exposed. This affects transaction admission (mempool) and RPC availability — a public-facing, remotely triggerable denial-of-service against validator/RPC nodes processing a single submitted transaction.

### Likelihood Explanation
Likelihood is high for any attacker capable of submitting a transaction or raw transaction bytes to a Kaia node: no special privileges, no prior account state, and no valid signature are strictly required to reach the vulnerable decode path (the decode occurs before validation/signature checks reject the transaction). Constructing the nested payload only requires standard RLP encoding knowledge.

### Recommendation
Enforce a maximum nesting/recursion depth (or reject any `AccountKeyTypeRoleBased` key type nested inside another during decoding, not only after decoding) directly inside `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, mirroring the existing `IsCompositeType`/`ErrNestedCompositeType` check but performed incrementally as each level is decoded rather than after the whole structure is materialized. Additionally, consider adding a global RLP decode-depth limit in `rlp.Stream` for composite/interface decoding paths used by untrusted transaction fields.

### Proof of Concept
Conceptually:
1. Construct `keyN = AccountKeyRoleBasedWithValues([legacyKey])` RLP-encoded via `AccountKeySerializer` (type=`AccountKeyTypeRoleBased`).
2. Iteratively wrap: `key(i) = AccountKeyRoleBasedWithValues([key(i-1)])`, repeating thousands of times (e.g., 50,000 levels), each adding only a few bytes.
3. Set this as the `Key` field of a `TxInternalDataAccountUpdate`, RLP-encode the full transaction (`toSerializable`).
4. Submit the raw transaction bytes to a node (e.g., via `eth_sendRawTransaction`/p2p broadcast). `TxInternalDataAccountUpdate.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recurse once per nesting level, exhausting the goroutine stack and crashing the node with `fatal error: stack overflow` before `CheckInstallable`/`CheckReplacable` ever run.

Note: exact required nesting depth to trigger stack overflow depends on Go runtime stack-frame size for the reflection-heavy RLP decoder and the node's configured max transaction/RLP size; this was not empirically measured in this review and would need to be confirmed by a Devin session with code execution access.

### Citations

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L211-230)
```go
func (a *AccountKeyRoleBased) CheckInstallable(currentBlockNumber uint64) error {
	// A zero-role key is not allowed.
	if len(*a) == 0 {
		return kerrors.ErrZeroLength
	}
	// Do not allow undefined roles.
	if len(*a) > (int)(RoleLast) {
		return kerrors.ErrLengthTooLong
	}
	for i := 0; i < len(*a); i++ {
		// A composite key is not allowed.
		if (*a)[i].IsCompositeType() {
			return kerrors.ErrNestedCompositeType
		}
		// If any key in the role cannot be initialized, return an error.
		if err := (*a)[i].CheckInstallable(currentBlockNumber); err != nil {
			return err
		}
	}
	return nil
```

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

**File:** api/api_kaia.go (L188-197)
```go
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
```

**File:** blockchain/types/tx_internal_data_account_update.go (L143-157)
```go
func (t *TxInternalDataAccountUpdate) fromSerializable(serialized *txInternalDataAccountUpdateSerializable) error {
	t.AccountNonce = serialized.AccountNonce
	t.Price = serialized.Price
	t.GasLimit = serialized.GasLimit
	t.From = serialized.From
	t.TxSignatures = serialized.TxSignatures

	serializer := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(serialized.Key, serializer); err != nil {
		return err
	}
	t.Key = serializer.GetKey()

	return nil
}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L292-301)
```go
func (t *TxInternalDataAccountUpdate) Validate(stateDB StateDB, currentBlockNumber uint64, onlyMutableChecks bool) error {
	oldKey := stateDB.GetKey(t.From)
	if err := accountkey.CheckReplacable(oldKey, t.Key, currentBlockNumber); err != nil {
		return err
	}
	if err := validate7702(stateDB, t.Type(), t.From, common.Address{}); err != nil {
		return err
	}
	return nil
}
```
