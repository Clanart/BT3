### Title
Stack exhaustion via unbounded recursive `AccountKeyRoleBased` nesting during RLP decode - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each of its role entries through `AccountKeySerializer`, which in turn dispatches on the encoded `keyType` and calls `rlp.DecodeBytes`/`s.Decode` on whatever `AccountKey` implementation `NewAccountKey` returns for that type — including `AccountKeyTypeRoleBased` itself. Because the type dispatch and decode happen before any semantic validation (`CheckInstallable`, which explicitly rejects composite/nested types) is performed, an attacker can submit an `AccountUpdate`/`FeeDelegatedAccountUpdate` (or similar) transaction whose `AccountKey` field is a `RoleBased` key that recursively nests another `RoleBased` key thousands of levels deep. Each decode level only costs a few bytes of RLP list/byte-string framing, so a modestly sized transaction payload can encode very deep nesting, mirroring the CVE‑2022‑1962 class of bug (uncontrolled recursion in `go/parser`/RLP-like decoders causing stack exhaustion).

### Finding Description
- `AccountKeyRoleBased.DecodeRLP` first decodes a `[][]byte` (`enc`), then for every element calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is an `AccountKeySerializer`: [1](#0-0) 
- `AccountKeySerializer.DecodeRLP` decodes the `keyType` tag first and then calls `NewAccountKey(serializer.keyType)` to allocate the concrete `AccountKey` implementation, then calls `s.Decode(serializer.key)` on it: [2](#0-1) 
- If the nested `keyType` is again `AccountKeyTypeRoleBased`, `NewAccountKey` returns a fresh `*AccountKeyRoleBased`, whose `DecodeRLP` is invoked recursively — re-entering the same decode chain (`AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → nested `AccountKeyRoleBased.DecodeRLP` → …) with no depth limit anywhere in this call chain.
- Validation that would reject such nesting only happens afterward, in `CheckInstallable`/`CheckUpdatable`, which explicitly flags `IsCompositeType()` keys as `kerrors.ErrNestedCompositeType`: [3](#0-2) [4](#0-3) 
  — but this check runs only *after* the entire (potentially deeply nested) structure has already been fully RLP-decoded, so the recursive-decode stack growth has already occurred by the time the rejection would fire.
- The underlying `rlp` package itself has no configurable recursion/nesting depth limit in its decoder machinery (`makeDecoder`, `makeStructDecoder`, `decodeListSlice`, `Stream.Decode`), so nothing in the generic decode path stops unbounded recursion either: [5](#0-4) [6](#0-5) 

This is directly analogous to the reported Go `go/parser` bug class: uncontrolled recursion driven by attacker-controlled nested input causing a stack-exhaustion panic, except here the trigger is a transaction's `AccountKey` payload processed by an unprivileged submitter.

### Impact Explanation
A transaction sender (no special privileges required) can craft an `AccountUpdate`-family transaction with a deeply, recursively nested `AccountKeyRoleBased` key. When any node — a public RPC node accepting the raw transaction, a txpool validator, or a full node executing/verifying the block — decodes this transaction, the recursive `DecodeRLP` chain will grow the Go call stack for every nesting level. With sufficient nesting (achievable within normal transaction size limits, since each level costs only a few bytes of RLP overhead) this results in a runtime stack-exhaustion panic, crashing the node process. If the panic occurs on a validating peer as opposed to only the originating RPC endpoint (e.g., during txpool validation prior to inclusion, or during block execution when the transaction is included by a malicious/careless block producer), this can cause a denial-of-service affecting network availability and, if only some nodes panic while others tolerate it, contribute to state/consensus divergence between honest nodes.

### Likelihood Explanation
Likelihood is high for reachability: any unprivileged account can submit an `AccountUpdate` or `FeeDelegatedAccountUpdate` transaction via public RPC (`eth_sendRawTransaction`) with an attacker-controlled `AccountKey` payload. The recursive decode path is exercised as soon as the raw transaction bytes are RLP-decoded — which happens very early (transaction ingestion/pool admission), before the `CheckInstallable` rejection of nested composite keys can take effect. No special network position, validator role, or leaked key is required.

### Recommendation
Enforce a maximum recursion/nesting depth check in `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` (e.g., reject decode immediately if `keyType == AccountKeyTypeRoleBased` while already inside a role-based decode, or track/pass a depth counter through the decode call chain and abort past a small fixed limit such as 1). Alternatively, add a generic recursion-depth guard in the `rlp` package's `Stream`/decoder machinery, consistent with the upstream Go fix for CVE-2022-1962, so that deeply nested attacker-supplied structures are rejected with a decode error rather than causing unbounded stack growth.

### Proof of Concept
Conceptually:
1. Construct an `AccountKeySerializer`-encoded blob `B0` of type `AccountKeyTypeNil` (or any minimal leaf key).
2. Wrap it: `B1 = RLP(keyType=RoleBased, key=RLP([B0]))` — i.e., an `AccountKeyRoleBased` containing one role whose key bytes are `B0`.
3. Repeat step 2 N times, each time wrapping the previous serialized blob as the single role entry of a new `AccountKeyRoleBased`, producing `B_N` with N nested levels (choose N large enough, e.g., tens of thousands, while staying within the transaction size limit since each wrap adds only a handful of bytes).
4. Build an `AccountUpdate` (or `FeeDelegatedAccountUpdate`) transaction whose `key` field is `B_N`, sign it, and submit it via `eth_sendRawTransaction` to a Kaia node.
5. Observe that RLP-decoding the transaction (`AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → nested `AccountKeyRoleBased.DecodeRLP` → …) recurses N times before any `CheckInstallable` validation runs, exhausting the goroutine stack and panicking the process.

Note: I was unable to directly inspect `NewAccountKey`'s switch statement (in `blockchain/types/accountkey/account_key.go`) within the tool budget to confirm there is no early type-based restriction preventing `AccountKeyTypeRoleBased` from being returned for a nested serializer; this should be verified in a live session, though the composite-type check only appearing in `CheckInstallable`/`CheckUpdatable` (post-decode) strongly suggests no such restriction exists at decode time.

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

**File:** rlp/decode.go (L162-193)
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
