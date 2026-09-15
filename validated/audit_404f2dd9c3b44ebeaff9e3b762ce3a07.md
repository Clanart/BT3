### Title
Uncontrolled recursive AccountKey decoding causes stack-overflow DoS in `eth_sendRawTransaction` / `kaia_sendRawTransaction` - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
`SendRawTransaction` and `SendRawTransactions` in `api/api_kaia_transaction.go` accept raw, attacker-controlled bytes from any unprivileged RPC caller and immediately RLP-decode them into a `types.Transaction` via `rlp.DecodeBytes(encodedTx, tx)` [1](#0-0) . For `TxTypeAccountUpdate` / `TxTypeFeeDelegatedAccountUpdate*` transactions, this eventually decodes an `AccountKey`, and if the key type is `AccountKeyTypeRoleBased`, decoding recurses without any depth limit.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte-strings, and for each element calls `rlp.DecodeBytes(b, &serializer)` on a fresh `AccountKeySerializer` [2](#0-1) . `AccountKeySerializer.DecodeRLP` reads the `keyType`, calls `NewAccountKey(serializer.keyType)` to allocate the concrete key object, and then calls `s.Decode(serializer.key)` [3](#0-2) . If `serializer.keyType` is again `AccountKeyTypeRoleBased`, this re-enters `AccountKeyRoleBased.DecodeRLP`, recursing into itself with no bound on nesting depth — analogous to `AccountKeySerializer.UnmarshalJSON` doing the same for the JSON path [4](#0-3) .

The only place that rejects a "nested RoleBased key" is `AccountKeyRoleBased.CheckInstallable`, which checks `IsCompositeType()` on each sub-key and returns `kerrors.ErrNestedCompositeType` [5](#0-4) . Critically, this semantic check only runs *after* the entire structure has already been fully decoded (confirmed by the test `TestAccountUpdateRoleBasedKeyNested`, which only observes the rejection at `txpool.AddRemote(tx)` time, i.e., post-decode) [6](#0-5) . Because the depth check happens strictly after decoding, an attacker can build an RLP payload whose `AccountKeyRoleBased` byte-strings contain another RLP-encoded `AccountKeyRoleBased`, nested to an attacker-chosen depth, and the recursive `DecodeRLP`/`rlp.DecodeBytes` call chain will exhaust the goroutine's stack (Go's recursion has no software depth guard here) before the `CheckInstallable`/pool-admission logic ever gets a chance to reject it.

This directly mirrors the reported LlamaIndex `JSONReader` bug class (CWE-674, uncontrolled recursion): parsing code recursively walks attacker-supplied nested structures with no depth cap, and the crash occurs strictly within the decode/parse step, before any application-level validation.

### Impact Explanation
A stack overflow in Go triggers a fatal, unrecoverable runtime crash (`runtime: goroutine stack exceeds ... — fatal error: stack overflow`), which cannot be caught by `recover()`. Since `SendRawTransaction`/`SendRawTransactions` are public RPC endpoints reachable by any unauthenticated/unprivileged sender submitting a single crafted transaction, this allows a remote attacker to crash any full node (or all nodes, if the malformed tx also propagates via p2p before failing decode) that processes the transaction — an availability (DoS) impact matching CVSS `AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H` from the source advisory.

### Likelihood Explanation
High. The attack requires only crafting a single RLP-encoded `AccountUpdate`-family transaction with a deeply nested `AccountKeyRoleBased` byte-string, encoding it as a raw transaction payload, and submitting it via a standard public JSON-RPC call (`sendRawTransaction`). No special privileges, prior state, valid signature verification order dependency, or timing conditions are needed — decoding runs unconditionally before signature/type checks reject the tx.

### Recommendation
Add an explicit recursion/nesting-depth counter (or a maximum decode-depth budget threaded through `rlp.Stream`) to `AccountKeyRoleBased.DecodeRLP` and `AccountKeySerializer.DecodeRLP` (and the JSON equivalents `UnmarshalJSON`) that rejects any `AccountKeyRoleBased` key type discovered while already decoding within another `AccountKeyRoleBased`, or more generally bound the total recursion depth (e.g., reject after depth > 1, since only one level of role-based keys is ever semantically valid) before any bytes for the inner element are decoded, not after the whole structure is built.

### Proof of Concept
1. Construct account key `K0 = AccountKeyPublic(pub0)`.
2. Construct `K1 = AccountKeyRoleBased([K0])`, RLP-encode via `AccountKeySerializer` to bytes `B1`.
3. Construct `K2 = AccountKeyRoleBased([RawKeyBytes(B1)])` — i.e., set a sub-key whose serialized form is itself `keyType=RoleBased, key=B1`, RLP-encode to `B2`.
4. Repeat step 3 recursively N times (e.g., N = 100,000), wrapping the previous serialized bytes each time, to produce `B_N`.
5. Build a `TxTypeAccountUpdate` transaction using `B_N` as the `Key` field, RLP-encode the full transaction to `rawTx`.
6. Call `eth_sendRawTransaction`/`kaia_sendRawTransaction` with `rawTx` against a target node.
7. The node's `rlp.DecodeBytes(encodedTx, tx)` call in `SendRawTransaction` recurses N times through `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP`, exceeding the goroutine stack and crashing the process with `fatal error: stack overflow` before any tx-pool or `CheckInstallable` validation is reached.

### Citations

**File:** api/api_kaia_transaction.go (L384-389)
```go
func (s *KaiaTransactionAPI) SendRawTransaction(ctx context.Context, encodedTx hexutil.Bytes) (common.Hash, error) {
	tx := new(types.Transaction)
	if err := rlp.DecodeBytes(encodedTx, tx); err != nil {
		return common.Hash{}, err
	}
	return submitTransaction(ctx, s.b, tx)
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

**File:** blockchain/types/accountkey/account_key_serializer.go (L84-103)
```go
func (serializer *AccountKeySerializer) UnmarshalJSON(b []byte) error {
	var keyJSON AccountKeyJSON

	if err := json.Unmarshal(b, &keyJSON); err != nil {
		return err
	}

	if keyJSON.KeyType == nil {
		return errNoKeyType
	}
	serializer.keyType = *keyJSON.KeyType

	var err error
	serializer.key, err = NewAccountKey(serializer.keyType)
	if err != nil {
		return err
	}

	return json.Unmarshal(keyJSON.Key, serializer.key)
}
```

**File:** tests/account_keytype_test.go (L1795-1822)
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
```
