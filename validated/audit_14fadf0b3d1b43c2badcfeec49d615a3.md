## Analog Found: Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` causes stack overflow crash from a single submitted transaction

### Title
Unbounded recursion in `AccountKeyRoleBased` RLP decoding leads to stack-overflow DoS from an unprivileged `TxTypeAccountUpdate` (or fee-delegated variant) transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
The Suricata CVE is a stack-overflow crash caused by unbounded recursive decompression of attacker-controlled nested input reached from a single request. The Kaia analog is unbounded recursion in the `AccountKey` RLP decoding path: `AccountKeyRoleBased.DecodeRLP` recursively decodes each element via `AccountKeySerializer`, and nothing prevents an attacker from nesting `AccountKeyRoleBased` values inside each other to an attacker-chosen depth, causing the decoder to recurse until the goroutine stack overflows — a fatal, unrecoverable Go runtime crash, not a catchable panic.

### Finding Description
`AccountKeySerializer.DecodeRLP` reads a key type then dispatches to `NewAccountKey(keyType)` followed by `s.Decode(serializer.key)`: [1](#0-0) 

If the decoded `keyType` is `AccountKeyTypeRoleBased`, this call chain lands in `AccountKeyRoleBased.DecodeRLP`, which decodes a list of raw byte strings and, for **each element**, constructs a brand new `AccountKeySerializer` and recursively calls `rlp.DecodeBytes` on it: [2](#0-1) 

Each recursive `AccountKeySerializer` can itself contain a `keyType` of `AccountKeyTypeRoleBased` again, so an attacker can nest `RoleBased` keys inside `RoleBased` keys to an arbitrary depth limited only by the outer RLP payload size (which is bounded only by transaction/RPC-payload size limits, not by decode depth). The RLP `Stream` implementation itself enforces no recursion-depth limit — it only tracks list-size bookkeeping on a `stack []uint64` slice, not a call-depth counter: [3](#0-2) 

Crucially, the semantic guard against nested composite types — `ErrNestedCompositeType` — is only enforced **after** the recursive decode has already completed, inside `CheckInstallable`/`CheckUpdatable`: [4](#0-3) 
and inside the API-side `checkAccountKeyZeroValues`: [5](#0-4) 

Because these checks run only after `DecodeRLP` returns, they cannot prevent the stack overflow that occurs *during* decoding itself. Any code path that RLP-decodes a raw transaction — e.g. `eth_sendRawTransaction`, transaction-pool ingestion (`AddRemote`), or block/transaction validation — will trigger the vulnerable recursive decode before any nested-type check executes, as illustrated by the existing regression test that only checks for `ErrNestedCompositeType` on a single level of nesting (not arbitrarily deep): [6](#0-5) 

The `AccountKey` payload is embedded as raw bytes inside `TxTypeAccountUpdate` and fee-delegated account-update transaction types (e.g. `TxInternalDataFeeDelegatedAccountUpdateWithRatio`), which are decoded via `rlp.DecodeBytes` from attacker-supplied bytes: [7](#0-6) 

### Impact Explanation
A Go stack overflow triggers a fatal runtime error (`runtime: goroutine stack exceeds ... — fatal error: stack overflow`), which is **not** a recoverable panic and crashes the entire process. Any node — a full node or an RPC-serving node — that decodes such a maliciously crafted `TxTypeAccountUpdate`/fee-delegated account-update transaction (via `eth_sendRawTransaction`, transaction-pool re-broadcast decode, or block execution) will crash. This is a network-wide availability impact (`AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`), matching the CVSS profile of the referenced Suricata CVE.

### Likelihood Explanation
Any unprivileged account can submit a `TxTypeAccountUpdate` (or fee-delegated variant) transaction to a public RPC endpoint. Constructing a deeply nested `AccountKeyRoleBased` structure requires only crafting the RLP bytes for the `Key` field — no signature validity is required to trigger the crash, since the crash happens during RLP decoding, which precedes signature/semantic validation.

### Recommendation
Enforce a maximum recursion/nesting depth check inside `AccountKeyRoleBased.DecodeRLP` (and generally in `AccountKeySerializer.DecodeRLP`) before recursing into nested `AccountKey` byte strings, rejecting any nested `RoleBased` (or excessively deep) structures immediately during decode rather than only after decode completes in `CheckInstallable`.

### Proof of Concept
Conceptually: build an `AccountKeyRoleBased` value whose single role entry is itself an RLP-encoded `AccountKeySerializer` of type `AccountKeyTypeRoleBased`, and repeat this nesting N times (e.g., tens of thousands of levels, still small in total byte size due to RLP's compact list encoding). Embed the resulting bytes as the `Key` field of a `TxTypeAccountUpdate` transaction and submit it via `eth_sendRawTransaction`. Decoding this transaction (`rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → repeat) recurses N times, exhausting the goroutine stack and crashing the node before `CheckInstallable`'s `ErrNestedCompositeType` check is ever reached.

*(Note: I could not find an explicit maximum RLP nesting-depth guard anywhere in `rlp/decode.go`, and could not fully verify whether Go's default stack growth limits (typically ~1GB max stack) would be hit before, say, a transaction-size limit would reject the payload first — this would need to be confirmed by an actual crash reproduction with real byte-size calculations, which requires execution environment access.)*

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

**File:** rlp/decode.go (L589-600)
```go
type Stream struct {
	r ByteReader

	remaining uint64   // number of bytes remaining to be read from r
	size      uint64   // size of value ahead
	kinderr   error    // error from last readKind
	stack     []uint64 // list sizes
	uintbuf   [32]byte // auxiliary buffer for integer decoding
	kind      Kind     // kind of value ahead
	byteval   byte     // value of single byte in type tag
	limited   bool     // true if input limit is in effect
}
```

**File:** api/api_kaia.go (L176-198)
```go
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
```

**File:** tests/account_keytype_test.go (L1793-1822)
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
```

**File:** blockchain/types/tx_internal_data_fee_delegated_account_update_with_ratio.go (L176-193)
```go
func (t *TxInternalDataFeeDelegatedAccountUpdateWithRatio) fromSerializable(serialized *txInternalDataFeeDelegatedAccountUpdateWithRatioSerializable) error {
	t.AccountNonce = serialized.AccountNonce
	t.Price = serialized.Price
	t.GasLimit = serialized.GasLimit
	t.From = serialized.From
	t.TxSignatures = serialized.TxSignatures
	t.FeePayer = serialized.FeePayer
	t.FeePayerSignatures = serialized.FeePayerSignatures
	t.FeeRatio = serialized.FeeRatio

	serializer := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(serialized.Key, serializer); err != nil {
		return err
	}
	t.Key = serializer.GetKey()

	return nil
}
```
