This confirms the recursion chain: `TxInternalDataAccountUpdate.DecodeRLP` [1](#0-0)  decodes raw tx bytes and calls `fromSerializable`, which invokes `rlp.DecodeBytes(serialized.Key, serializer)` on an `accountkey.AccountKeySerializer` [2](#0-1) . `AccountKeySerializer.DecodeRLP` reads the key type and calls `s.Decode(serializer.key)` [3](#0-2) . When `keyType` is `AccountKeyTypeRoleBased`, this recurses into `AccountKeyRoleBased.DecodeRLP`, which for every sub-element calls `NewAccountKeySerializer()` + `rlp.DecodeBytes(b, &serializer)` again [4](#0-3) . Nothing in this decode path enforces a nesting-depth limit — the "no nested RoleBased key" rule is only checked *after* decoding completes, in `CheckInstallable` (`IsCompositeType` check) [5](#0-4)  and in `checkAccountKeyZeroValues` in the RPC handler [6](#0-5) , and tested only for a single level of nesting in `TestAccountUpdateRoleBasedKeyNested` [7](#0-6) .

### Title
Unbounded recursive AccountKey RLP decoding allows stack-overflow DoS via crafted AccountUpdate transaction or RPC call - (File: blockchain/types/accountkey/account_key_serializer.go)

### Summary
The RLP decoding of `AccountKeyRoleBased` / `AccountKeySerializer` is mutually recursive with no depth limit, mirroring the `_.flatten`/`_.isEqual` unlimited-recursion bug class. A crafted `TxTypeAccountUpdate` (or fee-delegated variants) transaction, or a direct call to the public `kaia_decodeAccountKey`/`kaia_encodeAccountKey` RPC methods, can supply a deeply nested `RoleBased` account key structure that causes the Go call stack to overflow before any "nested composite type not allowed" validation runs.

### Finding Description
`AccountKeySerializer.DecodeRLP` decodes the key type, then recursively decodes into the concrete `AccountKey` implementation via `s.Decode(serializer.key)` [3](#0-2) . If the type is `AccountKeyTypeRoleBased`, `AccountKeyRoleBased.DecodeRLP` iterates the encoded list and, for each element, constructs a fresh `AccountKeySerializer` and calls `rlp.DecodeBytes` on it again [4](#0-3) . Because a `RoleBased` element can itself encode another `RoleBased` key, this decode path recurses once per nesting level present in attacker-supplied bytes, with no maximum depth check anywhere in the decode routines.

The only guard against nested `RoleBased` keys — `IsCompositeType`/`CheckInstallable` in `account_key_role_based.go` [5](#0-4) , and `checkAccountKeyZeroValues`'s `isNested` flag in the RPC layer [6](#0-5)  — runs *after* the full recursive decode has already completed. A sufficiently deep nesting depth (order of a few thousand levels, similar to the underscore.js PoC) will exhaust the goroutine stack and panic with `runtime: goroutine stack exceeds maximum size` inside the decode call, before the post-decode nesting check is ever reached.

This is directly reachable by:
1. An unprivileged transaction sender submitting a raw `TxTypeAccountUpdate` / `TxTypeFeeDelegatedAccountUpdate...` transaction whose RLP-encoded `Key` field contains deeply nested `RoleBased` sub-keys — decoded in `TxInternalDataAccountUpdate.DecodeRLP` → `fromSerializable` → `rlp.DecodeBytes(serialized.Key, serializer)` [8](#0-7) , which occurs during basic tx pool ingestion/block processing, prior to `Validate`.
2. A public RPC caller invoking `kaia_decodeAccountKey` with a crafted hex-encoded key blob, which calls `rlp.DecodeBytes(encodedAccKey, &dec)` directly [9](#0-8) .

### Impact Explanation
A crash from unbounded stack growth in a goroutine handling transaction decoding or RPC requests is a process-level Denial of Service. Since transaction/RLP decoding happens on the hot path for every incoming transaction (gossip, txpool admission, block re-execution), a single malicious transaction or RPC payload can crash a node process, and if broadcast, potentially many nodes simultaneously, satisfying the "Medium/High DoS" bar via `ErrDepth`-style attacks the codebase already explicitly guards against for EVM call depth (`evm.depth`, `params.CallCreateDepth`) but not for account-key RLP structures.

### Likelihood Explanation
Constructing the malicious payload requires no privileges, no valid signature (a stack overflow occurs during raw byte decoding, before signature/sender validation), and no special network position — only the ability to submit a transaction or call a public RPC method, which any external client can do. The attack requires only crafting nested RLP list bytes, which is straightforward and deterministic.

### Recommendation
Add an explicit recursion-depth counter/limit to `AccountKeySerializer.DecodeRLP` and `AccountKeyRoleBased.DecodeRLP` (e.g., reject decoding once nesting exceeds 2 levels, matching the "no RoleBased inside RoleBased" business rule) so the depth check happens *during* decode rather than only after it completes. Alternatively, thread an `rlp.Stream` depth/nesting limit through the decode call chain for composite `AccountKey` types.

### Proof of Concept
Construct an RLP `AccountKeySerializer` byte string where `keyType = AccountKeyTypeRoleBased` and its single list element is itself an `AccountKeySerializer` encoding of `AccountKeyTypeRoleBased` (recursively, e.g. ~5000 levels deep), analogous to the underscore.js `_.flatten`/`_.isEqual` PoC:
```go
// pseudo-code: build innermost non-composite key, then wrap N times
inner := accountkey.NewAccountKeyPublicWithValue(pub)
cur := accountkey.NewAccountKeySerializerWithAccountKey(inner)
for i := 0; i < 5000; i++ {
    roleBased := accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{ /* wrap cur's key via a new serializer round-trip */ })
    cur = accountkey.NewAccountKeySerializerWithAccountKey(roleBased)
}
encoded, _ := rlp.EncodeToBytes(cur)
// Submit `encoded` as the Key field of a TxTypeAccountUpdate transaction,
// or call kaia_decodeAccountKey(encoded) via RPC.
var dec accountkey.AccountKeySerializer
rlp.DecodeBytes(encoded, &dec) // crashes with stack overflow
```

### Citations

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

**File:** blockchain/types/tx_internal_data_account_update.go (L163-175)
```go
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

**File:** tests/account_keytype_test.go (L1796-1823)
```go
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
