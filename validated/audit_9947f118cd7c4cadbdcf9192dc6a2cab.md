### Title
Unbounded recursive RLP decoding of `AccountKeyRoleBased` allows stack-exhaustion DoS via a single crafted `TxTypeAccountUpdate`/fee-delegated account-update transaction - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
The Samba CVE-2020-10704 bug class is a stack overflow caused by recursively parsing an attacker-controlled, unbounded/deeply-nested request before any semantic validation occurs. Kaia has a structurally analogous path: `AccountKeyRoleBased.DecodeRLP` recursively re-invokes the full `AccountKeySerializer`/`AccountKey` decode chain for each sub-key, and this recursion is only bounded by nesting depth in the raw transaction bytes, not by an explicit depth limit. Nesting-depth checks (`kerrors.ErrNestedCompositeType`) are only applied *after* decoding succeeds, during `CheckInstallable`/`CheckUpdatable`, not during RLP decoding itself.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` first decodes the RLP value as a list of opaque byte strings (`[][]byte`), then for every element calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is an `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType`, constructs the concrete `AccountKey` via `NewAccountKey(keyType)`, and decodes into it: [2](#0-1) 

If the concrete key is itself `AccountKeyTypeRoleBased`, `s.Decode(serializer.key)` re-enters `AccountKeyRoleBased.DecodeRLP`, which repeats the same `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → `s.Decode` chain. This is genuine native Go call-stack recursion (not an iterative/queue-based walk), and each level adds several stack frames through the reflection-based RLP `decoder`/`makeDecoder` machinery: [3](#0-2) [4](#0-3) 

Crucially, the only defense against nested composite keys — `kerrors.ErrNestedCompositeType` — is enforced in `CheckInstallable`/`CheckUpdatable`, which run only *after* the whole structure has already been fully decoded: [5](#0-4) 

This mirrors the Samba flaw's root cause: the parser recurses on attacker-supplied nested structure before any bound/validation is applied, so a sufficiently deep — but still compact — encoding can exhaust the goroutine stack purely during decode, well before semantic rejection would occur. The existing regression test (`TestAccountUpdateRoleBasedKeyNested`) only exercises single-level nesting and asserts rejection via the *post-decode* semantic check, not via a decode-time depth guard: [6](#0-5) 

This decode path is directly reachable from `TxTypeAccountUpdate` and `TxTypeFeeDelegatedAccountUpdate*` transactions submitted by any unprivileged sender (e.g. via `eth_sendRawTransaction` / p2p tx propagation / `TxPool.AddRemote`), since these transaction types' `DecodeRLP` implementations call `rlp.DecodeBytes` on the serialized `Key` field before validation: [7](#0-6) [8](#0-7) 

### Impact Explanation
A crafted, deeply nested `AccountKeyRoleBased` payload inside a single signed (or even syntactically-valid-but-unsigned/garbage-signature, since decode happens before signature verification) `AccountUpdate`-family transaction can drive unbounded recursive RLP decoding on any node that receives it — full nodes relaying transactions, validators picking up the tx from the pool, and public RPC endpoints decoding raw transactions. This can crash the node process (goroutine stack exhaustion → fatal runtime error, unrecoverable via `recover()`), producing a denial-of-service against transaction-processing nodes network-wide when the crafted transaction is gossiped/broadcast. This matches the CVSS 7.5 "availability" impact of the reference CVE.

### Likelihood Explanation
Reachability is high: this requires only a single, small, syntactically valid RLP payload attached to a normal account-update transaction type and can be submitted by any unprivileged transaction sender or public RPC caller — no special privileges, consensus role, or peer status needed. Exploit feasibility depends on how much nesting is achievable within the node's overall transaction/RLP size limits (`blockchain/tx_pool.go` enforces an overall tx size cap) versus Go's dynamically growing goroutine stack (default max ~1GB); since each nesting level costs only a few bytes of RLP overhead, it is plausible to encode thousands of nesting levels within typical size limits, but I could not fully verify the exact numeric size cap in `blockchain/tx_pool.go`/`blockchain/error.go` within available exploration, so the precise threshold for triggering a crash (vs. merely slow decode) is uncertain and should be confirmed by a background engineer via testing.

### Recommendation
- Add an explicit recursion/nesting-depth limit inside `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` (e.g., reject if `keyType == AccountKeyTypeRoleBased` when already decoding a nested key context, or track and cap depth via a decode context), enforced *during* decoding rather than only in `CheckInstallable`.
- Alternatively, restructure decoding to be iterative rather than recursive across `AccountKeySerializer` boundaries.
- Add a fuzz/unit test that feeds thousands of nested `AccountKeyRoleBased` levels to `rlp.DecodeBytes`/`TxPool.AddRemote` and asserts a bounded, graceful error instead of a stack-exhaustion crash.

### Proof of Concept
Conceptual construction (to be validated by a background engineer with a live node):
1. Build an `AccountKeyRoleBased` key whose single sub-key is itself an `AccountKeyRoleBased` (as already exercised by `nestedAccKey` in `tests/account_keytype_test.go:1737-1739`), and repeat this nesting programmatically N times (e.g., N = 50,000) by wrapping `NewAccountKeySerializerWithAccountKey` output as the sole element of the next `AccountKeyRoleBased` level.
2. RLP-encode the resulting deeply nested key via `accountkey.NewAccountKeySerializerWithAccountKey` and embed it as the `Key` field of a `TxTypeAccountUpdate` transaction (following the same construction as `TestWronglyEncodedAccountKey` in `tests/kaia_test.go:439-521`, which manually builds the raw RLP transaction bytes).
3. Submit the resulting raw transaction bytes to `TxPool.AddRemote` (simulating `eth_sendRawTransaction` or p2p tx broadcast) and observe whether `rlp.DecodeBytes`/`tx.DecodeRLP` crashes the process via stack exhaustion instead of returning a decode/validation error.

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L211-231)
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
}
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

**File:** rlp/decode.go (L162-194)
```go
func makeDecoder(typ reflect.Type, tags rlpstruct.Tags) (dec decoder, err error) {
	kind := typ.Kind()
	switch {
	case typ == rawValueType:
		return decodeRawValue, nil
	case typ.AssignableTo(reflect.PtrTo(bigInt)):
		return decodeBigInt, nil
	case typ.AssignableTo(bigInt):
		return decodeBigIntNoPtr, nil
	case typ == reflect.PtrTo(u256Int):
		return decodeU256, nil
	case typ == u256Int:
		return decodeU256NoPtr, nil
	case kind == reflect.Ptr:
		return makePtrDecoder(typ, tags)
	case reflect.PtrTo(typ).Implements(decoderInterface):
		return decodeDecoder, nil
	case isUint(kind):
		return decodeUint, nil
	case kind == reflect.Bool:
		return decodeBool, nil
	case kind == reflect.String:
		return decodeString, nil
	case kind == reflect.Slice || kind == reflect.Array:
		return makeListDecoder(typ, tags)
	case kind == reflect.Struct:
		return makeStructDecoder(typ)
	case kind == reflect.Interface:
		return decodeInterface, nil
	default:
		return nil, fmt.Errorf("rlp: type %v is not RLP-serializable", typ)
	}
}
```

**File:** rlp/decode.go (L936-959)
```go
func (s *Stream) Decode(val interface{}) error {
	if val == nil {
		return errDecodeIntoNil
	}
	rval := reflect.ValueOf(val)
	rtyp := rval.Type()
	if rtyp.Kind() != reflect.Ptr {
		return errNoPointer
	}
	if rval.IsNil() {
		return errDecodeIntoNil
	}
	decoder, err := cachedDecoder(rtyp.Elem())
	if err != nil {
		return err
	}

	err = decoder(s, rval.Elem())
	if decErr, ok := err.(*decodeError); ok && len(decErr.ctx) > 0 {
		// Add decode target type to error so context has more meaning.
		decErr.ctx = append(decErr.ctx, fmt.Sprint("(", rtyp.Elem(), ")"))
	}
	return err
}
```

**File:** tests/account_keytype_test.go (L1795-1823)
```go
	// 2. Update an accountKey with a nested RoleBasedKey.
	{
		values := map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      anon.Nonce,
			types.TxValueKeyFrom:       anon.Addr,
			types.TxValueKeyGasLimit:   gasLimit,
			types.TxValueKeyGasPrice:   gasPrice,
			types.TxValueKeyAccountKey: nestedAccKey,
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeAccountUpdate, values)
		assert.Equal(t, nil, err)

		err = tx.SignWithKeys(signer, []*ecdsa.PrivateKey{roleKey.Keys[accountkey.RoleAccountUpdate]})
		assert.Equal(t, nil, err)

		// For tx pool validation test
		{
			err = txpool.AddRemote(tx)
			assert.Equal(t, kerrors.ErrNestedCompositeType, err)
		}

		// For block tx validation test
		{
			receipt, err := applyTransaction(t, bcdata, tx)
			assert.Equal(t, (*types.Receipt)(nil), receipt)
			assert.Equal(t, kerrors.ErrNestedCompositeType, err)
		}
	}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L159-175)
```go
func (t *TxInternalDataAccountUpdate) EncodeRLP(w io.Writer) error {
	return rlp.Encode(w, t.toSerializable())
}

func (t *TxInternalDataAccountUpdate) DecodeRLP(s *rlp.Stream) error {
	dec := newTxInternalDataAccountUpdateSerializable()

	if err := s.Decode(dec); err != nil {
		return err
	}
	if err := t.fromSerializable(dec); err != nil {
		logger.Warn("DecodeRLP failed", "err", err)
		return kerrors.ErrUnserializableKey
	}

	return nil
}
```

**File:** blockchain/types/tx_internal_data_fee_delegated_account_update.go (L183-195)
```go
func (t *TxInternalDataFeeDelegatedAccountUpdate) DecodeRLP(s *rlp.Stream) error {
	dec := newTxInternalDataFeeDelegatedAccountUpdateSerializable()

	if err := s.Decode(dec); err != nil {
		return err
	}
	if err := t.fromSerializable(dec); err != nil {
		logger.Warn("DecodeRLP failed", "err", err)
		return kerrors.ErrUnserializableKey
	}

	return nil
}
```
