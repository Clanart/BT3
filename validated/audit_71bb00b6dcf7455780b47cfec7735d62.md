### Title
Uncontrolled Recursion in `AccountKeyRoleBased` RLP Decoding Allows Remote DoS via Crafted `TxTypeAccountUpdate` Transaction - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes nested `AccountKeySerializer` entries with no depth or nesting-type check at decode time, mirroring the "uncontrolled recursion" bug class in CVE-2020-23804 (poppler crashing on deeply nested/self-referential PDF structures). A single unprivileged transaction sender can submit a `TxTypeAccountUpdate` transaction whose account key bytes recursively embed `AccountKeyRoleBased` inside `AccountKeyRoleBased`, causing unbounded recursive RLP decoding purely from parsing the transaction, before any semantic validation (`CheckInstallable`) rejects the structure.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a `[][]byte` list and, for each element, calls `rlp.DecodeBytes(b, &serializer)` on an `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType`, instantiates the corresponding `AccountKey` via `NewAccountKey`, and decodes into it with `s.Decode(serializer.key)`: [2](#0-1) 

If `keyType` is again `AccountKeyTypeRoleBased`, `s.Decode` invokes `AccountKeyRoleBased.DecodeRLP` again, and the cycle repeats — there is no depth limit anywhere in `NewAccountKey` or the serializer chain: [3](#0-2) 

This whole chain is reached from ordinary transaction decoding. `TxInternalDataAccountUpdate` stores its key as raw bytes and only converts it back into an `accountkey.AccountKey` inside `fromSerializable`, called directly from `DecodeRLP` — i.e., during the initial RLP decode of the transaction itself: [4](#0-3) 

The only defense against nested `RoleBased` keys, `CheckInstallable`/`ErrNestedCompositeType`, is enforced later, during state transition when the key is actually being installed into an account — not during RLP decoding: [5](#0-4) 

This is confirmed by the existing test that expects nested `RoleBased` keys to be *rejected only at tx-pool admission / execution time*, not at decode time — meaning decoding itself already fully materializes the nested structure before rejection: [6](#0-5) 

Because each nesting level only costs a small, constant number of RLP bytes (a type tag byte plus a wrapper list), an attacker can encode many nesting levels within a single transaction that still fits under ordinary transaction size limits, driving Go's native call stack recursion depth linearly with a small, RPC-submittable payload.

### Impact Explanation
Decoding a crafted `TxTypeAccountUpdate` transaction (or an `EncodeAccountKey`/`DecodeAccountKey` RPC call, which uses the identical serializer path) causes unbounded recursive function calls purely from RLP decoding logic: [7](#0-6) 
Sufficiently deep nesting can exhaust the goroutine stack, crashing or otherwise degrading the node process that decodes the transaction (state transition/day-to-day tx execution/available for public RPC callers who reach node's tx decode path), which is a denial-of-service against any node processing the transaction — including block producers relaying it and full nodes serving public RPC.

### Likelihood Explanation
Any unprivileged transaction sender or public RPC caller can construct this payload without special privileges, keys, or state — they only need to hand-craft the RLP bytes for the account key field (bypassing the normal `accountkey.NewAccountKeyRoleBasedWithValues` helper, which does not itself forbid nesting at construction time either). No consensus, staking, or governance privilege is required; the transaction only needs to be decoded (e.g., accepted into a node's tx pool, gossiped, or passed to `DecodeAccountKey`/`EncodeAccountKey` RPC) to trigger the recursive decode path.

### Recommendation
Add an explicit recursion-depth (or composite-type) check inside `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` itself — rejecting `AccountKeyTypeRoleBased` (or any composite type) as soon as it is encountered as a nested key, mirroring the `ErrNestedCompositeType` check that today happens only in `CheckInstallable`, so that the check runs during decoding rather than after the recursive structure has already been fully materialized.

### Proof of Concept
Conceptually:
1. Build an `AccountKeyRoleBased` value `K0` containing one `AccountKeyPublic`.
2. Build `K1 = AccountKeyRoleBased{K0}`, `K2 = AccountKeyRoleBased{K1}`, … recursively nesting `RoleBased` inside `RoleBased` for N levels (each level RLP-encoded via `NewAccountKeySerializerWithAccountKey` + `rlp.EncodeToBytes`, matching `EncodeRLP` in `account_key_role_based.go:110-118`).
3. Wrap `KN`'s bytes as the `Key` field of a `TxInternalDataAccountUpdate` transaction and sign it, or submit the raw bytes to `kaia_decodeAccountKey` RPC (`api/api_kaia.go:167-173`).
4. Submit the transaction to a node's tx pool / RPC endpoint. When the node calls `tx.DecodeRLP` (or the RPC handler calls `rlp.DecodeBytes`), the recursive `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` chain runs N times before `CheckInstallable` ever gets a chance to reject it, at N large enough to overflow or severely burden the goroutine stack.

Note: I was unable to execute this PoC or measure the exact N required to trigger a crash (stack limits, GC behavior, and any implicit RLP list-depth/size caps in `rlp.Stream` were not fully verified in the index); confirming the precise depth/byte-size threshold would require running the decode path in a live environment.

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

**File:** tests/account_keytype_test.go (L1793-1824)
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
