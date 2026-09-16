### Title
Unbounded recursive AccountKeyRoleBased decoding allows a single crafted AccountUpdate transaction to crash nodes before composite-type validation runs - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
CVE-2019-2455 is a MySQL parser bug where a low-privileged, network-reachable client can send crafted input that the parser fails to reject cleanly, crashing the server (availability-only DoS). The reachable Kaia analog is the `AccountKey` RLP parser: `AccountKeyRoleBased.DecodeRLP` recursively re-invokes the generic `AccountKeySerializer` decoder for every element it contains, with no recursion-depth limit, and the "no nested composite type" rule is enforced only *after* decoding succeeds — in `CheckInstallable`/`CheckUpdatable`, never inside `DecodeRLP` itself.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of opaque byte strings and, for each one, calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is a generic `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType` byte and, based on it, constructs the concrete key via `NewAccountKey` and recursively calls `s.Decode(serializer.key)`: [2](#0-1) 

Because `NewAccountKey` accepts `AccountKeyTypeRoleBased` as a valid type without restriction, an attacker can nest `AccountKeyRoleBased` values inside each other's byte-string elements to an arbitrary depth, driving unbounded recursive decoding (`AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → …) for every layer of nesting the attacker encodes.

The only defense against nested composite keys (`kerrors.ErrNestedCompositeType`) is implemented in `AccountKeyRoleBased.CheckInstallable`, which is invoked **after** the whole structure has already been fully decoded: [3](#0-2) 

This is confirmed by the existing regression test, which shows that decoding a nested `AccountKeyRoleBased` succeeds and the rejection (`ErrNestedCompositeType`) only occurs later, during transaction-pool/`applyTransaction` validation, i.e., strictly after `rlp.DecodeBytes`/`s.Decode` has already executed the recursive parsing: [4](#0-3) 

This RLP decode path is reached directly from unprivileged, publicly-callable transaction ingestion points, including `SendRawTransaction`/`SendRawTransactions` on the public RPC: [5](#0-4) 

as well as `TxInternalDataAccountUpdate`/`TxInternalDataFeeDelegatedAccountUpdate` decoding invoked for every transaction received over the network or the pool: [6](#0-5) 

### Impact Explanation
Every node that receives such a transaction — via `eth_sendRawTransaction`, `kaia_sendRawTransaction`, or normal p2p transaction propagation through `handleTxMsg` (which calls `msg.Decode(&txs)` on all received transactions before any semantic checks) — must fully RLP-decode the `AccountKey` field before it can be rejected. An attacker-controlled depth of nested `AccountKeyRoleBased` values forces deep Go-runtime recursion (multiple stack frames per nesting level, since each level goes through `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → reflection-based struct/interface decoding → `AccountKeySerializer.DecodeRLP`). This can exhaust the goroutine stack and cause a fatal, unrecoverable runtime crash (`fatal error: stack overflow`), which — unlike a Go `panic` — cannot be caught by `recover()`. A crash of this kind in the tx-pool ingestion path or block/message-processing path can propagate to every honest node that relays or validates the transaction, producing a network-wide denial of service, matching the "hang or frequently repeatable crash (complete DOS)" impact described in CVE-2019-2455.

### Likelihood Explanation
The attack requires only crafting and submitting a single `TxTypeAccountUpdate` (or fee-delegated variant) transaction with a deeply nested `AccountKeyRoleBased` value through a public RPC endpoint or the p2p transaction gossip protocol — no special privileges, valid nonce state, or prior account setup beyond a normally signed transaction is strictly needed to trigger the decode path (decoding happens before nonce/balance/signature-role checks complete). This is easily exploitable and repeatable, matching CVSS AC:L/PR:L/UI:N characteristics of the reference CVE.

### Recommendation
Enforce a maximum `AccountKey` nesting depth (reject `AccountKeyTypeRoleBased` when already inside a nested decode, and/or a global recursion counter) directly inside `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP`, before any element is recursively decoded — mirroring the existing composite-type restriction but performing it during parsing rather than only during `CheckInstallable`. Additionally, bound the total number of decode iterations/list depth in the generic `rlp.Stream` decoder for `Decoder`-implementing types reachable from transaction fields.

### Proof of Concept
Conceptually:
1. Build an `AccountKeyRoleBased` value `K0` containing a normal key (e.g., `AccountKeyPublic`).
2. Wrap `K0` inside another `AccountKeyRoleBased` `K1 = [Serialize(K0)]`, then `K2 = [Serialize(K1)]`, and repeat N times to build `K_N`.
3. RLP-encode `K_N` (`accountkey.NewAccountKeySerializerWithAccountKey(K_N)`), embed it as the `Key` field of a `TxTypeAccountUpdate` transaction, sign it validly, and submit it via `kaia_sendRawTransaction` or `eth_sendRawTransaction`.
4. On receipt, `rlp.DecodeBytes`/`tx.UnmarshalBinary` → `TxInternalDataAccountUpdate.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → recursively unwinds N levels of `AccountKeyRoleBased.DecodeRLP` before `CheckInstallable` ever runs, and for sufficiently large N causes the decoding goroutine to overflow its stack and crash the process.

Note: I was unable to execute this PoC directly in this environment (no code execution/tooling access here) to empirically determine the exact N needed to trigger a crash, or to confirm whether any transaction-size cap in `blockchain/tx_pool.go` (`ErrOversizedData`) is checked before or after RLP decoding for this field — the size-limit constant search only surfaced usages in tests and `tx_pool.go`, not the exact check order relative to `types.Transaction` decoding for this specific field. This should be verified by a Devin session with code-execution access, and if `ErrOversizedData` is enforced only on total transaction size (not decode recursion depth), the vulnerability path described above would still apply for any nesting depth achievable within that size limit.

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

**File:** tests/account_keytype_test.go (L1793-1823)
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

**File:** api/api_kaia_transaction.go (L382-417)
```go
// SendRawTransaction will add the signed transaction to the transaction pool.
// The sender is responsible for signing the transaction and using the correct nonce.
func (s *KaiaTransactionAPI) SendRawTransaction(ctx context.Context, encodedTx hexutil.Bytes) (common.Hash, error) {
	tx := new(types.Transaction)
	if err := rlp.DecodeBytes(encodedTx, tx); err != nil {
		return common.Hash{}, err
	}
	return submitTransaction(ctx, s.b, tx)
}

// SendRawTransactions will add multiple signed transactions to the transaction pool.
func (s *KaiaTransactionAPI) SendRawTransactions(ctx context.Context, inputs []hexutil.Bytes) ([]common.Hash, error) {
	hash := []common.Hash{}
	errs := []error{}

	if len(inputs) == 0 {
		hash = append(hash, common.Hash{})
		return hash, errors.New("Empty input")
	}

	for i, input := range inputs {
		if len(input) == 0 {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: empty input", i))
			break
		}
		// Allow naked Ethereum tx types
		if 0 < input[0] && input[0] < 0x7f {
			input = append([]byte{byte(types.EthereumTxTypeEnvelope)}, input...)
		}
		tx := new(types.Transaction)
		if err := rlp.DecodeBytes(input, tx); err != nil {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: %w", i, err))
			break
		}
```

**File:** blockchain/types/tx_internal_data_fee_delegated_account_update.go (L183-195)
```go
func (t *TxInternalDataFeeDelegatedAccountUpdate) DecodeRLP(s *rlp.Stream) error {
	dec := newTxInternalDataFeeDelegatedAccountUpdateSerializable()

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
