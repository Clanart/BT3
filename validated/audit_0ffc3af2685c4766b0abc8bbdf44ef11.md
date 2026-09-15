## Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` structures causes stack-overflow DoS - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
The `libass` advisory describes a stack overflow in `parse_tag` caused by recursive parsing of a crafted, deeply-nested file with no depth limit, discovered only when the payload is fully consumed. Kaia has a structurally identical pattern: `AccountKeyRoleBased.DecodeRLP` recursively decodes account-key byte blobs via `rlp.DecodeBytes` into `AccountKeySerializer`, which in turn can decode into another `AccountKeyRoleBased`, with no recursion-depth check performed before or during decoding. The "no nested composite type" rule is only enforced afterward, inside `CheckInstallable`/`checkAccountKeyZeroValues`, i.e. after the unbounded recursive parse has already occurred.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of opaque byte blobs and, for every element, calls `rlp.DecodeBytes(b, &serializer)`, where `serializer` is an `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type from the stream and then recursively decodes into whatever concrete `AccountKey` type that byte designates via `s.Decode(serializer.key)`: [2](#0-1) 

`NewAccountKey` will happily construct another `*AccountKeyRoleBased` for `AccountKeyTypeRoleBased`, so `s.Decode(serializer.key)` re-enters `AccountKeyRoleBased.DecodeRLP`, closing the recursive loop: [3](#0-2) 

The only defense against nesting ("roleBasedKey cannot contain a roleBasedKey as a role key") is applied *after* decoding completes, inside `checkAccountKeyZeroValues` (used by the `EncodeAccountKey`/`DecodeAccountKey` RPC path) and `CheckInstallable`/`CheckUpdatable` (used by tx-pool/state-transition validation): [4](#0-3) [5](#0-4) 

Because each nesting level only costs a small constant number of RLP bytes (list-header + key-type byte + inner list-header), an attacker can encode many thousands of nesting levels within an ordinary-sized payload, driving `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recursion far enough to exhaust the goroutine stack and crash the node process (Go's runtime fatally aborts on stack-overflow; this is not a recoverable panic).

This is directly reachable by an unprivileged caller through two paths:
1. **Public RPC, no transaction needed**: `KaiaAPI.DecodeAccountKey` calls `rlp.DecodeBytes(encodedAccKey, &dec)` directly on user-supplied bytes with no prior sanity/depth check: [6](#0-5) 
2. **Raw transaction submission**: `SendRawTransaction`/`SendRawTransactions` decode an arbitrary `types.Transaction`, and for `TxTypeAccountUpdate`/`TxTypeFeeDelegatedAccountUpdate` transactions, `fromSerializable` runs the same unbounded `rlp.DecodeBytes` on the `Key` field before any composite-type check: [7](#0-6) [8](#0-7) 

### Impact Explanation
A crash of the process handling the RPC request (full node, or any node serving `kaia_decodeAccountKey`/`eth_sendRawTransaction`) is a denial-of-service impact. Since `DecodeAccountKey` requires no signature, no gas, no state, and no prior admission checks, a single malicious RPC payload can crash any node that exposes this API — including nodes that also participate in block production/validation — potentially inducing DoS across the network if multiple nodes are targeted.

### Likelihood Explanation
High. No authentication, gas payment, valid signature, or account state is required to reach `DecodeAccountKey`; it is a pure RLP-decode of attacker-controlled bytes. Even via the `SendRawTransaction` path, the vulnerable decode happens as part of ordinary tx decoding in the tx pool / RPC layer, before signature or `CheckInstallable` validation rejects the nested structure.

### Recommendation
Enforce a maximum recursion/nesting depth for `AccountKey` decoding before or during `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP` (e.g., pass and decrement a depth counter, or reject decoding when `AccountKeyTypeRoleBased` is encountered while already inside a role-based decode), matching the intent of the existing `IsCompositeType`/`ErrNestedCompositeType` check but applied at decode-time rather than only at installability-check time. Alternatively, bound total RLP nesting depth generically in the `rlp` decoder for arbitrarily-typed streams reached via `AccountKey` interface decoding.

### Proof of Concept
Conceptually:
1. Construct `keyN = AccountKeySerializer{keyType: AccountKeyTypeRoleBased, key: AccountKeyRoleBased{}}` (an empty/degenerate role-based key), RLP-encode it to `bytesN`.
2. Construct `key(N-1) = AccountKeySerializer{keyType: AccountKeyTypeRoleBased, key: AccountKeyRoleBased{bytesN}}`, encode to `bytes(N-1)`.
3. Repeat this wrapping thousands of times (each level adds only a few bytes of overhead), producing a payload of nested `AccountKeyRoleBased` blobs many levels deep, well within normal RPC/tx size limits.
4. Call `kaia_decodeAccountKey` with the outermost `bytes0`, or submit a `TxTypeAccountUpdate` raw transaction whose `Key` field is `bytes0`, via `eth_sendRawTransaction`/`kaia_sendRawTransaction`.
5. `rlp.DecodeBytes` recurses through `AccountKeySerializer.DecodeRLP` ↔ `AccountKeyRoleBased.DecodeRLP` once per nesting level, exhausting the goroutine stack before `checkAccountKeyZeroValues`/`CheckInstallable` ever run, causing the node process to fatally crash.

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

**File:** api/api_kaia.go (L188-197)
```go
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

**File:** api/api_kaia_transaction.go (L384-389)
```go
func (s *KaiaTransactionAPI) SendRawTransaction(ctx context.Context, encodedTx hexutil.Bytes) (common.Hash, error) {
	tx := new(types.Transaction)
	if err := rlp.DecodeBytes(encodedTx, tx); err != nil {
		return common.Hash{}, err
	}
	return submitTransaction(ctx, s.b, tx)
```
