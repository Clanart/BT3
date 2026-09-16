Confirmed root cause: `AccountKeyRoleBased.DecodeRLP` decodes a list of encoded key blobs and, for each one, invokes `AccountKeySerializer.DecodeRLP`, which reads a key-type byte and dispatches via `NewAccountKey(serializer.keyType)` to allocate the concrete key type before calling `s.Decode(serializer.key)` [1](#0-0) [2](#0-1) . Since `AccountKeyType.AccountKeyTypeRoleBased` is one of the dispatchable types in `NewAccountKey` [3](#0-2) , an attacker can encode a byte blob whose key type is again `AccountKeyTypeRoleBased`, causing `AccountKeyRoleBased.DecodeRLP` to call `AccountKeySerializer.DecodeRLP` to call `AccountKeyRoleBased.DecodeRLP` again — unbounded mutual recursion driven purely by attacker-controlled nesting depth in the RLP payload, with no depth limit anywhere in the `rlp` package (`rlp/decode.go` has no depth counter) or in this decode path.

The composite-type restriction (`IsCompositeType()` / `ErrNestedCompositeType`) is enforced only in `CheckInstallable`/`CheckUpdatable`, which run *after* successful RLP decoding [4](#0-3) [5](#0-4) , so it does not prevent the recursive decode itself from exhausting the stack. This decode path is reached whenever any account-update transaction (`TxTypeAccountUpdate`, `TxTypeFeeDelegatedAccountUpdate`, etc.) is RLP-decoded, e.g. via `Transaction.DecodeRLP`/`UnmarshalBinary` used by the tx pool and RPC ingestion (`eth_sendRawTransaction`/`kaia_sendRawTransaction`) [6](#0-5) , before any semantic validation.

### Title
Stack Overflow via Unbounded Recursive Nesting of AccountKeyRoleBased During RLP Decoding - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes nested `AccountKey` blobs through `AccountKeySerializer.DecodeRLP` without any recursion/depth limit. Because `AccountKeyTypeRoleBased` is dispatchable from within `AccountKeySerializer.DecodeRLP`, an attacker can submit an account-update transaction whose key payload contains deeply self-nested `AccountKeyRoleBased` blobs, causing unbounded recursive decoding and a stack overflow / node crash, before any nested-composite-type validation (`CheckInstallable`) runs.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` reads a list of `[]byte` blobs, then for each blob calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is an `AccountKeySerializer` [1](#0-0) . `AccountKeySerializer.DecodeRLP` reads a `keyType` byte, constructs the corresponding `AccountKey` implementation via `NewAccountKey`, and decodes into it [2](#0-1) . Since `NewAccountKey` supports `AccountKeyTypeRoleBased` [3](#0-2) , an inner blob can itself be another `AccountKeyRoleBased`, re-entering `AccountKeyRoleBased.DecodeRLP` recursively. There is no depth counter anywhere in the `rlp` package's decoder machinery (`rlp/decode.go`) or in this accountkey decode chain, so nesting depth is bounded only by the size of the encoded payload, not by any explicit limit. A transaction can encode this nesting to an attacker-chosen depth within gas/tx-size limits, sufficient to exhaust the Go goroutine stack during RLP decoding, which happens prior to `CheckInstallable`'s single-level composite-type rejection check [4](#0-3) .

### Impact Explanation
An unauthenticated party can submit a single transaction (`TxTypeAccountUpdate` or any fee-delegated account-update variant) via public RPC or p2p tx propagation. Decoding it (in the tx pool's `AddLocal`/`AddRemote` path or during block processing) triggers unbounded recursive decoding, exhausting the goroutine stack and crashing the node process (denial of service across all nodes that decode the transaction, including full nodes and validators receiving it via tx propagation or blocks). This matches CWE-121 stack-based buffer overflow / stack exhaustion, analogous to the MaterialX MTLX nested `nodegraph` recursion bug.

### Likelihood Explanation
High likelihood: constructing such a transaction requires only crafting nested RLP blobs with type byte `AccountKeyTypeRoleBased` (value `5`) repeated at each nesting level, with no special privileges, keys, or preconditions — just enough gas/tx-size budget to encode the desired nesting depth. No signature validity is required for the crash to occur during decode, since `DecodeRLP` runs before signature/`CheckInstallable` validation.

### Recommendation
Introduce an explicit recursion/nesting-depth limit in `AccountKeyRoleBased.DecodeRLP` (and generally in the `rlp` package decode stream, tracking list/struct nesting depth) and reject with an error once a configured maximum depth (e.g., 1, since nested composite types are semantically invalid per `CheckInstallable`) is exceeded, performing this check during decode rather than only after decode succeeds.

### Proof of Concept
Construct an RLP blob for a `TxTypeAccountUpdate` transaction whose `AccountKeyRoleBased` key field recursively encodes `AccountKeySerializer{keyType: AccountKeyTypeRoleBased, key: <another AccountKeyRoleBased blob>}` nested to a large depth (e.g., tens of thousands of levels), each level wrapped as a `[]byte` list element per `AccountKeyRoleBased.EncodeRLP`'s format [7](#0-6) . Submitting this transaction for decoding (e.g., via `rlp.DecodeBytes`/`Transaction.UnmarshalBinary`) will recurse through `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` until the goroutine stack is exhausted, crashing the process.

### Citations

**File:** blockchain/types/accountkey/account_key_role_based.go (L110-118)
```go
func (a *AccountKeyRoleBased) EncodeRLP(w io.Writer) error {
	enc := make([][]byte, len(*a))

	for i, k := range *a {
		enc[i], _ = rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(k))
	}

	return rlp.Encode(w, enc)
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

**File:** blockchain/types/transaction.go (L239-266)
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

// UnmarshalBinary decodes the canonical encoding of transactions.
// It supports legacy RLP transactions and EIP2718 typed transactions.
func (tx *Transaction) UnmarshalBinary(b []byte) error {
	newTx := &Transaction{}
	if err := rlp.DecodeBytes(b, newTx); err != nil {
		return err
	}

	tx.setDecoded(newTx.data, len(b))
	return nil
}
```
