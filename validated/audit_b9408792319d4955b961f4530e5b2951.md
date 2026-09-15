### Title
Unbounded RoleBased account-key nesting causes pre-authentication stack overflow during transaction/RPC RLP decoding - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` and `AccountKeySerializer.DecodeRLP` recursively invoke each other with no depth limit when decoding an account key that contains a `RoleBased` key. Because a `RoleBased` key's `Key` field is itself stored/decoded as an opaque RLP byte blob that is fed back into a brand-new `rlp.DecodeBytes` call for `AccountKeySerializer`, an attacker can nest `RoleBased → RoleBased → RoleBased …` arbitrarily deep. This recursive decode happens **before** any structural validation (`CheckInstallable`/`checkAccountKeyZeroValues`, which reject nested composite types) is ever reached, and it also happens **before** signature verification in `Transaction.DecodeRLP`. This mirrors the CVE-2026-68073 bug class: unbounded type nesting parsed pre-authentication leading to `StackOverflowError`/stack overflow.

### Finding Description
- `AccountKeySerializer.DecodeRLP` decodes a `keyType`, constructs a concrete `AccountKey` via `NewAccountKey`, and then calls `s.Decode(serializer.key)`: [1](#0-0) 
- If the concrete type is `AccountKeyRoleBased`, its `DecodeRLP` reads a list of raw byte blobs and, for each element, starts a **new** `rlp.DecodeBytes` call into a fresh `AccountKeySerializer`: [2](#0-1) 
- That inner `AccountKeySerializer.DecodeRLP` call can again produce an `AccountKeyRoleBased`, which again decodes a list of byte blobs, each triggering another `rlp.DecodeBytes(..., &serializer)` — i.e., mutual, unbounded recursion between `AccountKeySerializer.DecodeRLP` and `AccountKeyRoleBased.DecodeRLP`.
- The only defense against nested `RoleBased` keys is `CheckInstallable`, which explicitly rejects "a composite key" as a role key: [3](#0-2)  — but this check runs only *after* the full recursive decode has already completed successfully (or crashed), during account-update application/tx-pool admission, not during `DecodeRLP` itself.
- Transactions carrying an `AccountKey` (`TxTypeAccountUpdate`, `TxTypeFeeDelegatedAccountUpdate`, `TxTypeFeeDelegatedAccountUpdateWithRatio`) store the key as a raw `[]byte` field and decode it via exactly this recursive path in `fromSerializable`: [4](#0-3) 
- Critically, `Transaction.DecodeRLP` performs the RLP decode of the whole transaction (which includes decoding the account-key field above) **before** it calls `SanityCheckSignatures`: [5](#0-4)  — meaning a syntactically valid but unsigned/garbage-signed transaction can still drive the vulnerable decode path.
- The same vulnerable decode primitive is also directly reachable, unauthenticated, through the public JSON-RPC method `kaia_decodeAccountKey`, which takes arbitrary raw bytes and RLP-decodes them into an `AccountKeySerializer` with zero validation: [6](#0-5) 
- No recursion-depth or stack-guard exists anywhere in this call chain; `rlp.Stream`/`makeDecoder` support arbitrary `Decoder`-implementing types recursively with no depth counter: [7](#0-6) 

### Impact Explanation
Excessive recursion depth in Go causes the goroutine to exceed the maximum stack size, resulting in a fatal, unrecoverable runtime stack-overflow crash (`runtime: goroutine stack exceeds ... - fatal error: stack overflow`), which cannot be caught by `recover()`. This crashes the entire node process. Reachable via:
1. A single crafted transaction with a `TxTypeAccountUpdate`/fee-delegated-account-update payload submitted to the public transaction pool (`eth_sendRawTransaction`)/gossip, hitting `Transaction.DecodeRLP` before signature verification.
2. The public RPC method `kaia_decodeAccountKey`, directly reachable by any caller with RPC access, no funds or signature required at all.

This is a pre-authentication, unauthenticated denial-of-service vector against any Kaia node (validator/full node/RPC node) that decodes attacker-supplied bytes, matching the High-severity bug class of CVE-2026-68073 ("unbounded type nesting ... pre-authentication ... denial of service").

### Likelihood Explanation
High. No signature, balance, or prior state is required. The payload can be constructed purely offline (nested `AccountKeyRoleBased` structures each wrapping another serialized `AccountKeySerializer`/`RoleBased` key), and per-nesting-level overhead is small, so a moderately sized payload (bounded by whatever tx-size/RPC-payload limits exist) can achieve very deep recursion. The composite-type nesting guard (`CheckInstallable`) exists in the codebase specifically because engineers anticipated *one* level of illegal nesting, but it is enforced only post-decode, not during decode, so it does not prevent the crash.

### Recommendation
- Add an explicit recursion-depth counter/limit inside `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` (e.g., pass down or track nesting depth and reject decoding beyond 1-2 levels, consistent with the "no nested composite key" invariant already enforced by `CheckInstallable`).
- Alternatively, reject decoding a `RoleBased` key whose elements themselves decode to `AccountKeyTypeRoleBased` (or any composite type) as soon as the sub-serializer's `keyType` is read, before recursing into `s.Decode(serializer.key)`, so the invariant is enforced during decode, not only after decode completes.
- Apply the same guard to `KaiaAPI.DecodeAccountKey` and JSON unmarshaling paths (`AccountKeySerializer.UnmarshalJSON`, `AccountKeyRoleBased.UnmarshalJSON`), since they share the same unbounded recursive structure.
- Consider enforcing this depth check generically in `rlp.Stream` for recursive `Decoder` implementations, or set `debug.SetMaxStack` awareness / bound total decode "work" per top-level call.

### Proof of Concept
Conceptually (exact byte-level PoC would require constructing nested RLP by tooling, not reproduced verbatim here since it needs iterative encode/decode against `accountkey.NewAccountKeySerializerWithAccountKey`):
1. Build `k0 = AccountKeyLegacy` (base case).
2. Repeat N times: `k_{i+1} = AccountKeyRoleBased{k_i}`, RLP-encode `AccountKeySerializer{keyType: RoleBased, key: k_{i+1}}` via `NewAccountKeySerializerWithAccountKey` + `rlp.EncodeToBytes` (mirrors `toSerializable` in `tx_internal_data_account_update.go`), producing progressively larger byte blobs, each just wrapping the previous one.
3. Set the final encoded bytes as the `Key` field of a `TxTypeAccountUpdate` transaction (or pass directly as the `encodedAccKey` parameter to the `kaia_decodeAccountKey` RPC call).
4. Submit the transaction via `eth_sendRawTransaction`/txpool (hits `Transaction.DecodeRLP` → `fromSerializable` → recursive `AccountKeySerializer`/`AccountKeyRoleBased` decode) or call `kaia_decodeAccountKey` directly with the bytes.
5. With sufficiently large N (bounded only by transaction/RPC payload size limits), the recursive decode call chain exhausts the goroutine stack and crashes the node process with a fatal stack overflow, before any signature check or `CheckInstallable` nested-type validation ever executes. [2](#0-1) [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** blockchain/types/transaction.go (L240-254)
```go
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

**File:** api/api_kaia.go (L166-173)
```go
// DecodeAccountKey gets an RLP encoded bytes of an account key and returns the decoded account key.
func (s *KaiaAPI) DecodeAccountKey(encodedAccKey hexutil.Bytes) (*accountkey.AccountKeySerializer, error) {
	dec := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(encodedAccKey, &dec); err != nil {
		return nil, err
	}
	return dec, nil
}
```

**File:** rlp/decode.go (L160-193)
```go
)

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
