### Title
Unbounded recursive `AccountKey` RLP decoding in `AccountKeyRoleBased.DecodeRLP` enables stack-overflow DoS via crafted `TxTypeAccountUpdate` transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` decodes each role's serialized key by recursively invoking `AccountKeySerializer.DecodeRLP`, which in turn calls `s.Decode(serializer.key)` on the concrete `AccountKey` type indicated by the encoded `keyType` byte [1](#0-0) [2](#0-1) . Nothing in the decode path prevents `keyType` from again being `AccountKeyTypeRoleBased`, so an attacker can nest `AccountKeyRoleBased` values inside each other to an arbitrary depth purely by crafting the raw bytes. This mirrors the CVE-2021-28302 bug class (unbounded recursive tree processing driven entirely by attacker-supplied structure, causing stack exhaustion), except here the recursion happens during RLP *decoding* rather than freeing.

### Finding Description
`AccountKeyRoleBased` is explicitly documented and checked as a "composite type" that must not be nested — `IsCompositeType()` returns `true` for it, and `CheckInstallable`/`CheckUpdatable` reject any role slot whose key `IsCompositeType()` is true [3](#0-2) . However, that guard is enforced only **after** the key has already been fully RLP-decoded. The decode path itself performs no depth check:

- `AccountKeyRoleBased.DecodeRLP` reads a `[][]byte` and, for each element, calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is a generic `AccountKeySerializer` [4](#0-3) .
- `AccountKeySerializer.DecodeRLP` first decodes a `keyType` tag, constructs the concrete key via `NewAccountKey(serializer.keyType)`, and then calls `s.Decode(serializer.key)` [2](#0-1) .
- If `keyType` is again `AccountKeyTypeRoleBased`, `s.Decode` dispatches straight back into `AccountKeyRoleBased.DecodeRLP`, repeating the cycle.

Because each nesting level only needs a small number of encoded bytes (a `keyType` tag plus an inner encoded role-based list), an attacker can encode thousands of nesting levels in a modestly sized transaction payload. Each level adds Go stack frames for `DecodeRLP` → `rlp.DecodeBytes` → `Stream.Decode` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → `s.Decode` → back into `AccountKeyRoleBased.DecodeRLP`, with no depth counter anywhere in `rlp.decode.go`'s generic decoder machinery to bound this [5](#0-4) [6](#0-5) .

### Impact Explanation
Deeply nested crafted `AccountKey` payloads reached through a `TxTypeAccountUpdate` (or fee-delegated account-update) transaction's key field would cause the decoding goroutine to recurse until the Go runtime stack limit is exceeded, crashing the process (fatal error: stack overflow, unrecoverable via `recover()`). Because transaction decoding happens during pool admission / block processing on every node that receives or re-executes the transaction, a single malicious transaction could crash multiple nodes simultaneously — a denial-of-service reachable from an unprivileged transaction sender, matching the allowed "AccountKey authorization" and "pool admission" categories.

### Likelihood Explanation
Likelihood is High for reachability (any account can submit a `TxTypeAccountUpdate`) but depends on whether pool/tx-size limits and per-field size caps allow enough nesting depth to actually exhaust the stack before other limits (e.g., max tx size, gas limit for calldata) are hit. This aspect could not be fully verified — I did not confirm the exact maximum number of nesting levels achievable within the network's max transaction size/gas limits, so exploitability should be validated empirically (e.g., by crafting an actual encoded payload and measuring achievable depth vs. required stack-overflow depth) before treating this as confirmed-exploitable in production.

### Recommendation
Add an explicit nesting/composite-type check inside `AccountKeyRoleBased.DecodeRLP` (or in `AccountKeySerializer.DecodeRLP`) that rejects `AccountKeyTypeRoleBased`-typed inner keys immediately upon decoding the `keyType` tag, before recursing into `s.Decode`, rather than deferring this check to `CheckInstallable`/`CheckUpdatable`. Additionally, consider adding a generic recursion-depth guard to `rlp.Stream`/`decodeDecoder` for custom `Decoder` implementations to prevent similar issues in other custom RLP decoders.

### Proof of Concept
Conceptually: construct an RLP-encoded `AccountKeyRoleBased` list whose single element is itself an RLP-encoded `[keyType=AccountKeyTypeRoleBased, innerRoleBasedBytes]`, and repeat this nesting N times (e.g., N in the tens of thousands, achievable within the tx-size limits since each level costs only a few bytes of overhead). Submit this as the `key` field of a `TxTypeAccountUpdate` transaction. When the node decodes the transaction (`rlp.DecodeBytes`/`s.Decode` on the tx, which cascades into `AccountKeyRoleBased.DecodeRLP`), the decode call stack grows by several frames per nesting level with no depth limit, until the goroutine stack overflows and the process crashes. I was not able to execute this PoC to confirm the exact N required to trigger a crash — this would need to be validated in a running node/test harness.

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

**File:** rlp/decode.go (L269-301)
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
```

**File:** rlp/decode.go (L518-542)
```go
var ifsliceType = reflect.TypeFor[[]interface{}]()

func decodeInterface(s *Stream, val reflect.Value) error {
	if val.Type().NumMethod() != 0 {
		return fmt.Errorf("rlp: type %v is not RLP-serializable", val.Type())
	}
	kind, _, err := s.Kind()
	if err != nil {
		return err
	}
	if kind == List {
		slice := reflect.New(ifsliceType).Elem()
		if err := decodeListSlice(s, slice, decodeInterface); err != nil {
			return err
		}
		val.Set(slice)
	} else {
		b, err := s.Bytes()
		if err != nil {
			return err
		}
		val.Set(reflect.ValueOf(b))
	}
	return nil
}
```
