### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` account keys causes stack-overflow DoS from a raw transaction - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively invokes `rlp.DecodeBytes`/`AccountKeySerializer.DecodeRLP` for each sub-key without any depth limit or "is this a composite type" check at decode time. A crafted `TxTypeAccountUpdate` (or fee-delegated variants) transaction whose `AccountKey` field is a deeply self-nested `AccountKeyRoleBased` structure can drive this recursion to a depth sufficient to exhaust the goroutine stack, crashing the node with an unrecoverable Go runtime "stack overflow" fatal error — directly analogous to the recursive-descent stack overflow in CVE-2020-36370 (`parse_unary`).

### Finding Description
`AccountKeySerializer.DecodeRLP` dispatches on `keyType` and calls `s.Decode(serializer.key)`: [1](#0-0) 

When `keyType == AccountKeyTypeRoleBased`, the resulting `key` is an `*AccountKeyRoleBased`, whose own `DecodeRLP` method decodes a list of raw byte strings and, for **each element**, constructs a brand new `AccountKeySerializer` and calls `rlp.DecodeBytes` again: [2](#0-1) 

Nothing in this decode path rejects a sub-key that is itself `AccountKeyTypeRoleBased`. The only place that rejects "nested composite" account keys (`ErrNestedCompositeType`) is in **validation** logic that runs after decoding — `AccountKeyRoleBased.CheckInstallable`/`CheckUpdatable`: [3](#0-2) 
and the JSON-only helper `checkAccountKeyZeroValues` used by the `kaia_encodeAccountKey` RPC: [4](#0-3) 

Neither of these checks is consulted during `DecodeRLP`, so recursive decoding of an attacker-supplied nested `RoleBased` key happens unconditionally, before any semantic validation, purely as a consequence of parsing the wire bytes — the same bug class as `parse_unary`'s unbounded recursive descent in CVE-2020-36370.

This is directly reachable from a single submitted transaction: `Transaction.DecodeRLP` → `TxInternalDataSerializer.DecodeRLP` → the specific `TxInternalDataAccountUpdate*` decoder → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` (recursing): [5](#0-4) [6](#0-5) 

The public entry points for this are the raw-transaction RPC handlers, which decode attacker bytes with no pre-decode nesting/size sanity check beyond overall byte length: [7](#0-6) [8](#0-7) 

### Impact Explanation
A Go "stack overflow" fatal error (triggered when a goroutine's stack exceeds the runtime maximum) is **not** recoverable via `defer`/`recover`; it always terminates the process. Any Kaia node (validator, endpoint node, or any node accepting `eth_sendRawTransaction`/`kaia_sendRawTransaction`, or that relays/re-decodes the pooled transaction to other peers) that decodes such a crafted transaction will crash. Because transaction bytes are gossiped and re-decoded by every peer that receives them, a single malicious raw transaction can cause a network-wide denial of service across all nodes that process it — matching the "Availability: High" impact of the reference CVE (CVSS AV\:L/AC\:L/PR\:N/UI\:R… A\:H analog, here reachable remotely via RPC/tx propagation rather than local file parsing).

### Likelihood Explanation
Reachable from a single unprivileged, unauthenticated call: any address can construct an `AccountUpdate`-family transaction, sign it with their own key, RLP-encode it with a self-referential nested `AccountKeyRoleBased` (`RoleBased -> [RoleBased -> [RoleBased -> ... ]]`), and submit it via the public `SendRawTransaction`/`SendRawTransactions` RPC. No special privileges, governance state, or contract deployment is required — only that the raw byte size be large enough (bounded by whatever RPC/p2p message-size limit is enforced, e.g. `MaxRequestContentLength`/`MaxTxDataSize`) to reach a recursion depth that exhausts the stack. Because each nesting level requires only a few bytes of RLP list overhead, a multi-megabyte payload (well within typical JSON-RPC payload limits) can reach very deep recursion.

### Recommendation
Add an explicit recursion-depth counter (or reject any sub-key of `AccountKeyRoleBased`/`AccountKeyWeightedMultiSig` whose type is itself composite) directly inside `AccountKeyRoleBased.DecodeRLP`/`AccountKeySerializer.DecodeRLP`, before recursing into `rlp.DecodeBytes`, mirroring the `IsCompositeType`/`ErrNestedCompositeType` check that currently only runs post-decode in `CheckInstallable`/`CheckUpdatable`. Additionally, consider adding a general nesting-depth guard to the `rlp` package decoder for recursive `Decoder` implementations, and reject account-update transactions early (before full RLP materialization) if their RLP payload exceeds a small fixed bound for account-key data.

### Proof of Concept
1. Construct `AccountKeyRoleBased{ AccountKeyRoleBased{ AccountKeyRoleBased{ ... } } }` nested N times (N large enough, e.g. tens/hundreds of thousands, bounded by the max tx/RPC payload size).
2. Build a `TxTypeAccountUpdate` transaction whose `AccountKey` value is this nested structure and sign it with a normal, funded account's key (see construction pattern in `TestAccountUpdateRoleBasedKeyNested`, which builds one level of nesting via `accountkey.NewAccountKeyRoleBasedWithValues`): [9](#0-8) 
3. RLP-encode the transaction (`rlp.EncodeToBytes(tx)`) and submit it via `kaia_sendRawTransaction`/`eth_sendRawTransaction`.
4. The receiving node's `rlp.DecodeBytes(encodedTx, tx)` call in `SendRawTransaction` recurses through `AccountKeyRoleBased.DecodeRLP` N times, exhausting the goroutine stack and crashing the process with a fatal, unrecoverable stack-overflow error — before the `ErrNestedCompositeType` validation in `CheckInstallable` is ever reached.

*Note on uncertainty:* I could not fully verify from the index the exact numeric values of `MaxRequestContentLength`/`MaxTxDataSize` or confirm whether a pre-decode byte-size cap exists that would make the required nesting depth impractical to reach in practice. This bounds confidence on exploitability at scale; a Devin session with full repository/runtime access would be needed to measure the concrete achievable recursion depth against the enforced payload-size limits and confirm the crash empirically.

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L211-230)
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
```

**File:** api/api_kaia.go (L176-199)
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
	return nil
```

**File:** blockchain/types/transaction.go (L239-254)
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

**File:** api/api_kaia_transaction.go (L384-390)
```go
func (s *KaiaTransactionAPI) SendRawTransaction(ctx context.Context, encodedTx hexutil.Bytes) (common.Hash, error) {
	tx := new(types.Transaction)
	if err := rlp.DecodeBytes(encodedTx, tx); err != nil {
		return common.Hash{}, err
	}
	return submitTransaction(ctx, s.b, tx)
}
```

**File:** api/api_kaia_transaction.go (L392-427)
```go
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
		if err := s.b.SendTx(ctx, tx); err != nil {
			hash = append(hash, common.Hash{})
			errs = append(errs, fmt.Errorf("Index %d: %w", i, err))
			break
		}
		hash = append(hash, tx.Hash())
	}

	return hash, errors.Join(errs...)
}
```

**File:** tests/account_keytype_test.go (L1733-1745)
```go
	// roleBasedKeys and a nested roleBasedKey
	roleKey, err := createDefaultAccount(accountkey.AccountKeyTypeRoleBased)
	assert.Equal(t, nil, err)

	nestedAccKey := accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{
		roleKey.AccKey,
	})

	if testing.Verbose() {
		fmt.Println("reservoirAddr = ", reservoir.Addr.String())
		fmt.Println("roleAddr = ", roleKey.Addr.String())
	}

```
