### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` account keys allows stack-overflow DoS from an unprivileged raw transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
Kaia's `AccountKeyRoleBased.DecodeRLP` mutually recurses with `AccountKeySerializer.DecodeRLP` with no depth limit, exactly analogous to the Apache Commons Lang `ClassUtils.getClass()` uncontrolled-recursion bug (CVE-2025-48924, CWE-674). Any account-key-bearing transaction (`AccountUpdate`, `AccountCreation`, `FeeDelegatedAccountUpdate*`) submitted through a public RPC endpoint (e.g. `eth_sendRawTransaction`) is RLP-decoded before the transaction size/type checks in `TxPool.validateTx` run, so an attacker can force unbounded decode-time recursion using a crafted raw transaction blob.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte strings and, for every element, constructs a fresh `AccountKeySerializer` and RLP-decodes into it: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type and then dispatches to `NewAccountKey(type)` followed by `s.Decode(serializer.key)`. If the type is again `AccountKeyTypeRoleBased`, this calls back into `AccountKeyRoleBased.DecodeRLP`, creating unbounded mutual recursion with no depth counter: [2](#0-1) [3](#0-2) 

The only guard against nested `RoleBased` keys is a *post-decode* semantic check — `CheckInstallable`/`CheckUpdatable` reject a `RoleBased` key whose sub-key `IsCompositeType()` is true, and `checkAccountKeyZeroValues` (used only by the `EncodeAccountKey` RPC, not by transaction ingestion) rejects nesting for a similar reason: [4](#0-3) [5](#0-4) 

These checks run only *after* the entire nested structure has already been fully RLP-decoded (i.e., after the recursive stack has already unwound), so they cannot prevent the recursion itself. Likewise, `TxInternalDataAccountUpdate.fromSerializable`/`DecodeRLP` (and the equivalent for `AccountCreation`/`FeeDelegatedAccountUpdate*`) call `rlp.DecodeBytes` on the key bytes unconditionally, before any size/type sanity checks: [6](#0-5) 

The transaction-size guard `MaxTxDataSize` (128KB) that would otherwise bound the encoded payload is only enforced in `TxPool.validateTx`, which runs on the already-decoded `*types.Transaction` object — i.e., strictly *after* the vulnerable recursive decode has already executed: [7](#0-6) [8](#0-7) 

Because each nesting level only costs a handful of RLP bytes (a 1-byte key-type tag plus small list/string headers), an attacker can encode tens of thousands of nested `AccountKeyRoleBased` levels within a payload far smaller than typical RPC body-size limits, well before the 128KB `MaxTxDataSize` check is ever reached. The generic `rlp` decoder itself has no recursion-depth limiting anywhere in its reflection-based decode dispatch (`makeDecoder`/`decodeInterface`/`decodeListSlice`), so nothing in the decode path stops the recursion: [9](#0-8) [10](#0-9) 

### Impact Explanation
A Go runtime stack overflow triggers a fatal, unrecoverable process crash (`runtime: goroutine stack exceeds ... limit`), which `recover()` cannot catch. Because this can be reached from any transaction that carries an `AccountKey` (account creation/update, fee-delegated variants) submitted by an ordinary, unprivileged sender via the public JSON-RPC `sendRawTransaction` endpoint, an attacker can crash a full node or validator node with a single crafted (but never actually broadcast/mined) transaction blob — a state-divergence/availability risk affecting consensus-participating nodes, which the report scope explicitly treats as in-scope ("state divergence between honest nodes ... acceptance of an invalid transaction").

### Likelihood Explanation
High: no privileged access, staking, or governance rights are required — only the ability to submit a raw RLP-encoded transaction to a node's public RPC. Constructing arbitrarily deep nested `RoleBased` account keys requires only a few bytes per nesting level, so a payload comfortably within common RPC/HTTP body limits (well under `MaxTxDataSize`) can achieve tens of thousands of recursion levels.

### Recommendation
Add an explicit recursion-depth limit inside `AccountKeyRoleBased.DecodeRLP` (and more generally inside `AccountKeySerializer.DecodeRLP`), rejecting keys nested beyond one level *before* recursing further, mirroring the composite-type restriction that today is enforced only after decoding. Alternatively, thread a depth counter through the RLP `Stream` for `Decoder`-based recursive types, or enforce `MaxTxDataSize`/`stream size limit` checks earlier — e.g. via `rlp.NewStream(r, limit)` — combined with an explicit "no composite key inside composite key" check performed at decode time rather than only at `CheckInstallable`/`CheckUpdatable` time.

### Proof of Concept
1. Construct a byte sequence `payload` representing `AccountKeySerializer{keyType: AccountKeyTypeRoleBased, key: AccountKeyRoleBased{ AccountKeySerializer{keyType: AccountKeyTypeRoleBased, key: ...} } }`, nested N times (N in the tens of thousands), where the innermost key is a trivial `AccountKeyNil`/`AccountKeyLegacy`.
2. Wrap `payload` as the `Key` field of a `TxInternalDataAccountUpdate` (or `AccountCreation`/`FeeDelegatedAccountUpdate*`), sign it, RLP-encode the full transaction (kept under common RPC body-size limits, e.g. a few MB, yet far exceeding the logical 128KB `MaxTxDataSize` semantic bound since that check has not run yet).
3. Submit the raw transaction bytes via the public `eth_sendRawTransaction`/`klay_sendRawTransaction` RPC to a node.
4. Observe `rlp.DecodeBytes` → `TxInternalDataAccountUpdate.DecodeRLP` → `AccountKeySerializer.DecodeRLP` ⇄ `AccountKeyRoleBased.DecodeRLP` recursing N times before any `MaxTxDataSize` or `CheckInstallable`/`CheckUpdatable` composite-type check executes, causing the node's goroutine stack to grow unboundedly and eventually crash the process with a fatal, unrecoverable runtime error.

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

**File:** api/api_kaia.go (L188-198)
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
	}
```

**File:** blockchain/types/tx_internal_data_account_update.go (L143-175)
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

**File:** blockchain/tx_pool.go (L57-62)
```go
	// MaxTxDataSize is the maximum size a single transaction can have. This field has
	// non-trivial consequences: larger transactions are significantly harder and
	// more expensive to propagate; larger transactions also take more resources
	// to validate whether they fit into the pool or not.
	// TODO-Kaia: Change the name to clarify what it means. It means the max length of the transaction.
	MaxTxDataSize = 4 * txSlotSize // 128KB
```

**File:** blockchain/tx_pool.go (L884-889)
```go
	// Reject transactions over MaxTxDataSize to prevent DOS attacks
	// Note: Sidecar are not included in the size calculation.
	// Sidecar-specific validation must be done elsewhere.
	if uint64(tx.SizeWithoutBlobTxSidecar()) > MaxTxDataSize {
		return ErrOversizedData
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

**File:** rlp/decode.go (L520-533)
```go
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
```
