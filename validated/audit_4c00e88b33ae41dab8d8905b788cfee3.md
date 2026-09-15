### Title
Unbounded recursive `AccountKeyRoleBased` RLP decoding allows stack-exhaustion DoS via a single `AccountUpdate`-family transaction - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively re-invokes the generic `AccountKeySerializer` decoder for every element of a role-based key, and that serializer can itself resolve to another `AccountKeyRoleBased`. There is no depth limit enforced before or during this recursive decode, so an attacker can submit a single, cheaply crafted `AccountUpdate` / `FeeDelegatedAccountUpdate*` transaction whose account-key field nests thousands of `AccountKeyRoleBased` layers, forcing unbounded recursive parsing on every node/RPC endpoint that decodes the raw transaction.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes an outer `[][]byte`, then for each byte-slice element constructs a fresh `AccountKeySerializer` and calls `rlp.DecodeBytes` on it: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type, instantiates the corresponding `AccountKey` via `NewAccountKey`, and decodes into it: [2](#0-1) 

If the nested type resolved is again `AccountKeyTypeRoleBased`, this chain (`AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → …) recurses with no depth check. The only structural safeguard, `CheckInstallable`, rejects composite (nested) role-based keys — but it runs only *after* decoding completes, during transaction execution/validation, not during the RLP decode itself: [3](#0-2) 

This decode path is reached for any transaction type carrying an `AccountKey` payload as soon as the transaction is deserialized — e.g. via `TxInternalDataAccountUpdate.DecodeRLP` / `TxInternalDataAccountUpdate.fromSerializable`, which is invoked whenever a raw transaction is decoded by the tx pool, p2p ingestion, or `eth_sendRawTransaction`/`kaia_sendRawTransaction` RPC handlers: [4](#0-3) [5](#0-4) 

Because decoding happens before signature verification or `CheckInstallable`/pool admission checks, an attacker does not need a valid signature, sufficient balance, or a correct account state to trigger the recursive decode — the cost is paid purely by submitting bytes that parse as a deeply nested `AccountKeyRoleBased` structure. This mirrors the CVE-2022-3810 bug class: a parser (`AP4_File`/here, `AccountKeySerializer`/`AccountKeyRoleBased`) recursively processes attacker-supplied nested structures without a recursion-depth guard, and a remote, unauthenticated party can trigger it with a single crafted input.

### Impact Explanation
Excessive recursive decoding can exhaust the goroutine stack, triggering Go's unrecoverable "stack overflow" fatal error (not a `panic` catchable by `recover`), which terminates the entire node process — a genuine denial of service, not merely elevated CPU usage. Because the decode occurs in the shared transaction-ingestion path (tx pool `AddRemote`, p2p transaction propagation, and RPC raw-transaction submission), any Kaia node reachable by an unprivileged transaction sender or public-RPC caller is affected.

### Likelihood Explanation
The current transaction size cap (`MaxTxDataSize`, referenced in `blockchain/tx_pool.go`) bounds the encoded payload size, which in turn bounds how many `AccountKeyRoleBased` nesting levels an attacker can fit into one transaction (each nesting level costs only a few bytes of RLP list/string headers, so a ~32KB payload could still encode several thousand nested levels). Whether this specific depth is sufficient to overflow a Go goroutine's stack (which grows dynamically up to a large default limit) could not be conclusively verified from the available code and would need empirical testing (crafting the payload and observing whether the node crashes or merely experiences a CPU/latency spike). This uncertainty should be resolved before treating this as a confirmed crash-level DoS versus a lesser resource-consumption issue.

### Recommendation
Enforce an explicit maximum nesting/recursion depth (e.g., reject any `AccountKeyRoleBased` whose elements decode to another composite/`AccountKeyRoleBased` type) directly inside `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, before recursing, rather than only in the post-decode `CheckInstallable` check. This should mirror the `IsCompositeType`/`ErrNestedCompositeType` check performed in `CheckInstallable` but apply it eagerly during decoding so that decoding a nested-composite key fails immediately rather than after unbounded recursion.

### Proof of Concept
Conceptual construction (recursion depth `N`, bounded only by `MaxTxDataSize`):
1. Encode key type `AccountKeyTypeNil`/simple key at the innermost level.
2. Wrap it as `AccountKeyRoleBased` containing one element: `rlp.EncodeToBytes([]interface{}{AccountKeyTypeRoleBased, innerEncodedBytes})`.
3. Repeat step 2, wrapping the previous result, `N` times until the total payload approaches `MaxTxDataSize`.
4. Place the resulting bytes as the `key` field of an `AccountUpdate` (or `FeeDelegatedAccountUpdate*`) transaction body, as shown in the existing test harness pattern for malformed account keys: [6](#0-5) 
5. Submit the raw transaction via `AddRemote`/RPC. `rlp.DecodeBytes` will recurse `N` times through `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` before any signature or `CheckInstallable` validation occurs, exercising the unbounded-recursion path described above.

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

**File:** blockchain/types/tx_internal_data_serializer.go (L69-96)
```go
func (serializer *TxInternalDataSerializer) DecodeRLP(s *rlp.Stream) error {
	if err := s.Decode(&serializer.txType); err != nil {
		// fallback to the original transaction decoding.
		txd := newEmptyTxInternalDataLegacy()
		if err := s.Decode(txd); err != nil {
			return err
		}
		serializer.txType = TxTypeLegacyTransaction
		serializer.tx = txd
		return nil
	}

	if serializer.txType == EthereumTxTypeEnvelope {
		var ethType TxType
		if err := s.Decode(&ethType); err != nil {
			return err
		}
		serializer.txType = serializer.txType<<8 | ethType
	}

	var err error
	serializer.tx, err = NewTxInternalData(serializer.txType)
	if err != nil {
		return err
	}

	return s.Decode(serializer.tx)
}
```

**File:** tests/kaia_test.go (L473-521)
```go
	// case 1. AccountUpdate
	{
		tx := types.NewTx(&types.TxInternalDataAccountUpdate{})
		txtype := types.TxTypeAccountUpdate

		wrongEncodedKey := []byte{0x10}
		serializedBytes, err := rlp.EncodeToBytes([]interface{}{
			txtype,
			uint64(0),
			new(big.Int).SetUint64(25 * params.Gkei),
			uint64(100000),
			*bcdata.addrs[0],
			wrongEncodedKey,
		})
		require.Equal(t, nil, err)

		h := rlpHash(struct {
			Byte    []byte
			ChainId *big.Int
			R       uint
			S       uint
		}{
			serializedBytes,
			bcdata.bc.Config().ChainID,
			uint(0),
			uint(0),
		})
		sig, err := types.NewTxSignaturesWithValues(signer, tx, h, []*ecdsa.PrivateKey{bcdata.privKeys[0]})
		if err != nil {
			panic(err)
		}

		buffer := new(bytes.Buffer)
		err = rlp.Encode(buffer, txtype)
		assert.Equal(t, nil, err)

		err = rlp.Encode(buffer, []interface{}{
			uint64(0),
			new(big.Int).SetUint64(25 * params.Gkei),
			uint64(100000),
			*bcdata.addrs[0],
			wrongEncodedKey,
			sig,
		})
		require.Equal(t, nil, err)

		err = rlp.DecodeBytes(buffer.Bytes(), tx)
		require.Equal(t, kerrors.ErrUnserializableKey, err)
	}
```
