### Title
StackOverflowError via unbounded recursive RLP decoding of nested `AccountKeyRoleBased` in `TxTypeAccountUpdate` transactions - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
An unprivileged transaction sender can craft an `TxTypeAccountUpdate` transaction whose `Key` field encodes an `AccountKeyRoleBased` value that recursively contains other `AccountKeyRoleBased` values many levels deep. Because RLP/AccountKey decoding recurses without any depth limit, and the "no nested composite type" rule (`ErrNestedCompositeType`) is enforced only *after* decoding completes (in `CheckInstallable`/`CheckUpdatable`), a deeply nested key crashes the decoding node with a `StackOverflowError` (Go: stack growth/OOM panic) before any validation ever runs. This mirrors the `ion-java` bug class (CVE-2024-21634): unbounded recursive deserialization of untrusted, self-describing nested data leading to stack exhaustion.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a `[][]byte` and, for every element, independently RLP-decodes an `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type, constructs a new `AccountKey` via `NewAccountKey`, and then decodes into it — and `NewAccountKey` allows `AccountKeyTypeRoleBased` itself: [2](#0-1) [3](#0-2) 

This means each element of a `RoleBased` key's byte slices can itself be an RLP-encoded `RoleBased` key, and decoding recurses: `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `s.Decode(serializer.key)` → `AccountKeyRoleBased.DecodeRLP` → … indefinitely, with no depth counter anywhere in this chain.

The transaction-level entry point that triggers this is `TxInternalDataAccountUpdate.DecodeRLP`, which decodes the `Key` bytes via `rlp.DecodeBytes(serialized.Key, serializer)` inside `fromSerializable`: [4](#0-3) 

This `DecodeRLP` runs whenever an `TxTypeAccountUpdate` transaction is RLP-decoded — i.e., when it arrives via `eth_sendRawTransaction`/`klay_sendRawTransaction`, via p2p transaction propagation, or during block/receipt processing. Decoding happens unconditionally, before any semantic validation.

The only defense against nested `RoleBased` keys is `IsCompositeType()` checked in `AccountKeyRoleBased.CheckInstallable`/`CheckUpdatable`, which is invoked by tx-pool admission (`AddRemote`) and state-transition application — both of which run only **after** the RLP decode has already completed successfully: [5](#0-4) 

Existing tests confirm the nested-key rejection is a post-decode, application-level check (`ErrNestedCompositeType`), not a decode-time depth guard: [6](#0-5) 

Because RLP decoding uses ordinary Go function-call recursion (reflection-driven `decoder` closures in `rlp/decode.go`, plus the custom `DecodeRLP` methods above) with no maximum-depth enforcement, an attacker can nest tens of thousands of `RoleBased` wrappers to exhaust the goroutine stack, crashing the node (or the RPC-serving goroutine) with a runtime `fatal error: stack overflow`, which is not recoverable via `recover()` in Go.

### Impact Explanation
A single crafted, syntactically well-formed `TxTypeAccountUpdate` transaction (no valid signature or balance required, since the crash happens during pure decoding, prior to signature/fee checks) can crash any Kaia node that decodes it — this includes:
- Full nodes decoding transactions received over p2p gossip,
- RPC nodes processing `eth_sendRawTransaction`/`klay_sendRawTransaction`,
- Nodes replaying/verifying blocks containing such a transaction.

A Go `stack overflow` is a fatal, non-recoverable runtime error that terminates the process, unlike a panic that can be caught — this makes it a denial-of-service against public RPC endpoints and full/validator nodes that accept transactions from the network, satisfying the "state divergence" / node-crash impact bar for a High-severity, reachable-from-a-single-transaction bug.

### Likelihood Explanation
Likelihood is high: constructing the malicious payload only requires standard RLP encoding tools (no cryptographic material, no special privileges) — an attacker generates N nested `AccountKeyRoleBased` byte-encodings and wraps them in a normal `TxInternalDataAccountUpdate` RLP envelope, then submits it via public RPC or gossip. No signature verification or balance check occurs before the vulnerable decode path executes.

### Recommendation
- Add an explicit recursion/nesting depth limit to `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP` (e.g., reject decoding if `AccountKeyTypeRoleBased` is encountered while already inside a `RoleBased` decode context), enforced *before* recursing, not only after via `CheckInstallable`.
- Alternatively, thread a decode-depth counter through the `rlp.Stream`/decoding call chain for custom `Decoder` implementations, aborting with an error once a small fixed maximum (e.g., depth 2, matching the legitimate single level of nesting) is exceeded.
- Apply the same fix pattern to any other custom `DecodeRLP` implementations that can recursively invoke themselves via attacker-controlled type tags (verify `AccountKeyWeightedMultiSig` and any similar composite/self-referential RLP `Decoder`s for the same issue).

### Proof of Concept
Conceptually (Go-like pseudocode) using this repo's `accountkey` and `rlp` packages:

```go
// Build a deeply nested RoleBased key bottom-up.
inner := accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{
    accountkey.NewAccountKeyPublicWithValue(pub),
})
var cur accountkey.AccountKey = inner
for i := 0; i < 200000; i++ { // depth sufficient to exhaust default goroutine stack
    cur = accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{cur})
}

keyBytes, _ := rlp.EncodeToBytes(accountkey.NewAccountKeySerializerWithAccountKey(cur))

// Embed into a TxTypeAccountUpdate-style RLP tx and submit via
// eth_sendRawTransaction / klay_sendRawTransaction, or feed it directly to
// rlp.DecodeBytes(keyBytes, accountkey.NewAccountKeySerializer())
// -> triggers unbounded recursion through
//    AccountKeySerializer.DecodeRLP -> AccountKeyRoleBased.DecodeRLP -> ...
// -> fatal error: stack overflow (unrecoverable process crash)
```

The exact depth needed depends on default goroutine stack growth limits (Go grows goroutine stacks up to ~1GB by default before crashing), but is easily reachable with an RLP payload on the order of a few hundred KB to a few MB, well within normal transaction size limits.

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
