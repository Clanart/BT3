### Title
Unbounded recursive AccountKey/RLP decoding allows stack-exhaustion DoS via a single `TxTypeAccountUpdate` transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
Kaia's RLP decoder (`rlp/decode.go`) and the `AccountKey` decoding stack (`AccountKeySerializer`, `AccountKeyRoleBased`) decode nested/composite structures purely via recursive function calls with no depth limit, mirroring the exact bug class of CVE-2019-11937 (Mcrouter's Carbon protocol reader stack exhaustion from a large/deeply-nested struct). A transaction sender can submit a `TxTypeAccountUpdate` transaction whose `AccountKey` field encodes deeply nested `AccountKeyRoleBased` values; because the composite-type rejection (`ErrNestedCompositeType`) is only enforced *after* full RLP decoding completes (in `CheckInstallable`/`CheckUpdatable`), the recursive decode itself can be driven arbitrarily deep before that check is ever reached.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes each role slot as an independent RLP blob and immediately re-enters generic RLP decoding for it via `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type and then calls `s.Decode(serializer.key)` on whatever `AccountKey` implementation `NewAccountKey` returns: [2](#0-1) 

If `serializer.key` is itself an `AccountKeyRoleBased`, this calls back into `AccountKeyRoleBased.DecodeRLP`, which calls `rlp.DecodeBytes` again on each nested slot — an unbounded mutual recursion between `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` … driven entirely by attacker-supplied nesting depth in the transaction payload.

The composite-type/nesting restriction only exists as a semantic check performed *after* decoding is complete, in `CheckInstallable`/`CheckUpdatable`: [3](#0-2) [4](#0-3) 

This is confirmed by the existing test, which shows the nested-key rejection (`kerrors.ErrNestedCompositeType`) happening at `txpool.AddRemote` / block application time, i.e., strictly after the transaction (and its nested `AccountKey`) has already been RLP-decoded: [5](#0-4) 

The underlying generic RLP decoder itself provides no recursion/depth guard either — struct, slice, and interface decoders (`makeStructDecoder`, `makeListDecoder`/`decodeListSlice`, `decodeInterface`) are implemented as directly recursive Go functions over `reflect.Value`, with no depth counter, matching the same architectural weakness that caused CVE-2019-11937 in Mcrouter's Carbon reader: [6](#0-5) [7](#0-6) 

Because `Transaction.DecodeRLP` (used both for pool admission via `AddRemote` and for full block execution) drives this decoding path directly on attacker-controlled bytes before any semantic validation runs, an attacker-controlled transaction body can force arbitrarily deep recursion: [8](#0-7) 

### Impact Explanation
A crafted `TxTypeAccountUpdate` transaction with deeply nested `AccountKeyRoleBased` values can be broadcast to any public node's tx-pool endpoint (`eth_sendRawTransaction`/`AddRemote`) or included by a block producer, causing the decoding goroutine to recurse until the Go runtime's stack limit is exhausted, crashing (or severely degrading) the node process. Because tx-pool admission and block processing both invoke the same recursive `DecodeRLP` path before any composite-type check, a single unprivileged sender can trigger this on every node that processes/relays the transaction — a network-wide denial-of-service, consistent with the "High"/availability-only severity of the original CVE (CVSS AV:N/AC:L/PR:N/UI:N with Availability:High impact).

### Likelihood Explanation
The path is reachable by any unprivileged transaction sender: constructing a `TxTypeAccountUpdate` with a deeply nested composite `AccountKey` requires only standard tx-building/signing capability, no special privileges, and can be sized so that the recursion depth needed to exhaust a goroutine's stack fits well within normal transaction gas/size limits (each nesting level only adds a small serialized RLP wrapper). No cryptographic or race-condition tricks are required, making exploitation straightforward and repeatable.

### Recommendation
Enforce the existing `IsCompositeType`/nesting restriction *during* decoding rather than only after decoding completes — e.g., track and bound recursion depth in `AccountKeyRoleBased.DecodeRLP`/`AccountKeySerializer.DecodeRLP` (reject nested `AccountKeyRoleBased` inside `AccountKeyRoleBased` at decode time), and/or add a general depth limit to the RLP `Stream`/decoder (`rlp/decode.go`) so that any recursively-decoded structure (struct, slice, interface) is bounded, independent of the specific Go type being decoded.

### Proof of Concept
1. Build an `AccountKeyRoleBased` value `K0` containing a single role slot whose `AccountKey` is itself an `AccountKeyRoleBased` `K1`, whose slot is another `AccountKeyRoleBased` `K2`, and so on, nested N times (N large enough, e.g., tens of thousands, to exceed the default goroutine stack).
2. RLP-encode this nested structure via `AccountKeyRoleBased.EncodeRLP` (repeated application of `NewAccountKeySerializerWithAccountKey`) and embed it as the `AccountKey` field of a `TxTypeAccountUpdate` transaction, sign it with a valid sender key.
3. Submit the raw transaction to a node's public RPC (`eth_sendRawTransaction`) or peer tx-pool.
4. `Transaction.DecodeRLP` → `TxInternalDataSerializer.DecodeRLP` → account-update tx decoder → `AccountKeyRoleBased.DecodeRLP` ⇄ `AccountKeySerializer.DecodeRLP` recurse N times before any `CheckInstallable`/`CheckUpdatable` validation runs, exhausting the stack and crashing/hanging the node process.

Note: exact stack-overflow depth threshold depends on Go runtime stack-growth limits (`debug.SetMaxStack`) configured for the node binary; this was not independently verified in the repository's node startup configuration and should be confirmed experimentally.

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L233-269)
```go
func (a *AccountKeyRoleBased) CheckUpdatable(newKey AccountKey, currentBlockNumber uint64) error {
	if newKey, ok := newKey.(*AccountKeyRoleBased); ok {
		lenOldKey := len(*a)
		lenNewKey := len(*newKey)
		// If no key is to be replaced, it is regarded as a fail.
		if lenNewKey == 0 {
			return kerrors.ErrZeroLength
		}
		// Do not allow undefined roles.
		if lenNewKey > (int)(RoleLast) {
			return kerrors.ErrLengthTooLong
		}
		for i := range lenNewKey {
			switch {
			// A composite key is not allowed.
			case (*newKey)[i].IsCompositeType():
				return kerrors.ErrNestedCompositeType
			// If newKey is longer than oldKey, init the new attributes.
			case i >= lenOldKey:
				if err := (*newKey)[i].CheckInstallable(currentBlockNumber); err != nil {
					return err
				}
			// Do nothing for AccountKeyTypeNil
			case (*newKey)[i].Type() == AccountKeyTypeNil:

			// Check whether the newKey is replacable or not
			default:
				if err := CheckReplacable((*a)[i], (*newKey)[i], currentBlockNumber); err != nil {
					return err
				}
			}
		}
		return nil
	}
	// Update is not possible if the type is different.
	return kerrors.ErrDifferentAccountKeyType
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

**File:** tests/account_keytype_test.go (L1793-1823)
```go
	txpool := blockchain.NewTxPool(blockchain.DefaultTxPoolConfig, bcdata.bc.Config(), bcdata.bc, bcdata.govModule)

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

**File:** rlp/decode.go (L269-316)
```go
func makeListDecoder(typ reflect.Type, tag rlpstruct.Tags) (decoder, error) {
	etype := typ.Elem()
	if etype.Kind() == reflect.Uint8 && !reflect.PtrTo(etype).Implements(decoderInterface) {
		if typ.Kind() == reflect.Array {
			return decodeByteArray, nil
		}
		return decodeByteSlice, nil
	}
	etypeinfo := theTC.infoWhileGenerating(etype, rlpstruct.Tags{})
	if etypeinfo.decoderErr != nil {
		return nil, etypeinfo.decoderErr
	}
	var dec decoder
	switch {
	case typ.Kind() == reflect.Array:
		dec = func(s *Stream, val reflect.Value) error {
			return decodeListArray(s, val, etypeinfo.decoder)
		}
	case tag.Tail:
		// A slice with "tail" tag can occur as the last field
		// of a struct and is supposed to swallow all remaining
		// list elements. The struct decoder already called s.List,
		// proceed directly to decoding the elements.
		dec = func(s *Stream, val reflect.Value) error {
			return decodeSliceElems(s, val, etypeinfo.decoder)
		}
	default:
		dec = func(s *Stream, val reflect.Value) error {
			return decodeListSlice(s, val, etypeinfo.decoder)
		}
	}
	return dec, nil
}

func decodeListSlice(s *Stream, val reflect.Value, elemdec decoder) error {
	size, err := s.List()
	if err != nil {
		return wrapStreamError(err, val.Type())
	}
	if size == 0 {
		val.Set(reflect.MakeSlice(val.Type(), 0, 0))
		return s.ListEnd()
	}
	if err := decodeSliceElems(s, val, elemdec); err != nil {
		return err
	}
	return s.ListEnd()
}
```

**File:** rlp/decode.go (L407-439)
```go
func makeStructDecoder(typ reflect.Type) (decoder, error) {
	fields, err := structFields(typ)
	if err != nil {
		return nil, err
	}
	for _, f := range fields {
		if f.info.decoderErr != nil {
			return nil, structFieldError{typ, f.index, f.info.decoderErr}
		}
	}
	dec := func(s *Stream, val reflect.Value) (err error) {
		if _, err := s.List(); err != nil {
			return wrapStreamError(err, typ)
		}
		for i, f := range fields {
			err := f.info.decoder(s, val.Field(f.index))
			if err == EOL {
				if f.optional {
					// The field is optional, so reaching the end of the list before
					// reaching the last field is acceptable. All remaining undecoded
					// fields are zeroed.
					zeroFields(val, fields[i:])
					break
				}
				return &decodeError{msg: "too few elements", typ: typ}
			} else if err != nil {
				return addErrorContext(err, "."+typ.Field(f.index).Name)
			}
		}
		return wrapStreamError(s.ListEnd(), typ)
	}
	return dec, nil
}
```

**File:** blockchain/types/transaction.go (L239-254)
```go
// DecodeRLP implements rlp.Decoder
func (tx *Transaction) DecodeRLP(s *rlp.Stream) error {
	serializer := newTxInternalDataSerializer()
	if err := s.Decode(serializer); err != nil {
		return err
	}

	if !SanityCheckSignatures(serializer.tx.RawSignatureValues(), serializer.tx.Type()) {
		return ErrInvalidSig
	}

	size := calculateTxSize(serializer.tx)
	tx.setDecoded(serializer.tx, int(size))

	return nil
}
```
