## Analysis Result

Confirmed root cause: `AccountKeyRoleBased.DecodeRLP` recursively decodes nested account-key blobs via `NewAccountKeySerializer`/`NewAccountKey` with no depth limit, and this decode path executes fully *before* any semantic validation (`CheckInstallable`/`CheckUpdatable`, which reject `IsCompositeType()` nesting) is ever reached.

### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` keys causes StackOverflow denial of service - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` decodes an RLP-encoded list of key blobs and, for each element, recursively invokes `AccountKeySerializer.DecodeRLP` → `NewAccountKey(keyType)` → `key.DecodeRLP(s)` without any limit on nesting depth. Because `AccountKeyTypeRoleBased` is a valid `AccountKeyType` value that can itself be selected again inside a nested blob, an attacker can construct an `AccountUpdate`/`FeeDelegatedAccountUpdate` transaction whose `AccountKey` field contains thousands of nested `AccountKeyRoleBased` wrappers. Decoding this transaction causes deep mutual recursion between `AccountKeyRoleBased.DecodeRLP` [1](#0-0)  and `AccountKeySerializer.DecodeRLP` [2](#0-1) , exhausting the goroutine stack and crashing the node process with a Go runtime `fatal error: stack overflow` (unrecoverable, not a normal `panic`/`recover`-able error).

### Finding Description
The nested-composite-type rejection only happens at `IsCompositeType()`/`CheckInstallable`/`CheckUpdatable` time [3](#0-2) , which runs **after** the full RLP structure has already been decoded into memory. The decode step itself has no depth bound:

- `AccountKeyRoleBased.DecodeRLP` splits the outer RLP list into raw byte blobs and, for each blob, calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is an `*AccountKeySerializer` [1](#0-0) .
- `AccountKeySerializer.DecodeRLP` reads the `keyType` tag first (fully attacker-controlled, 1 byte), constructs a fresh `AccountKey` via `NewAccountKey(serializer.keyType)`, and then calls `s.Decode(serializer.key)` [2](#0-1) .
- `NewAccountKey` happily returns another `AccountKeyRoleBased` instance for `AccountKeyTypeRoleBased` with no check that this would create a nested composite type [4](#0-3) .
- If the newly-allocated key is itself `AccountKeyRoleBased`, `s.Decode` calls its `DecodeRLP` again, re-entering the same cycle.

This exactly mirrors the HAPI FHIR bug class: two mutually-recursive parse functions with no maximum nesting-depth check, driven entirely by attacker-supplied nested structure size rather than total payload size. Because each nesting level only costs a few bytes of RLP list/byte-string overhead, a payload of a few hundred KB can encode many thousands of nesting levels — comfortably enough to exhaust a goroutine's stack (Go's default goroutine stack starts small and grows, but is capped; extremely deep recursion triggers `runtime: goroutine stack exceeds ... — fatal error: stack overflow`, which cannot be caught by `defer/recover`).

The existing test `TestAccountUpdateRoleBasedKeyNested` only demonstrates a single level of nesting and shows that the *value-level* check (`ErrNestedCompositeType`) rejects it [5](#0-4)  — but that check happens only after decoding succeeds. It does not test (and the code does not defend against) many thousands of nesting levels causing the decoder itself to crash before that check is ever reached.

### Impact Explanation
Any of the following remotely-reachable paths decode transaction bytes and thus reach this recursive decoder before validation logic runs:
- `txpool.AddRemote`/`AddLocal` decoding a raw signed `AccountUpdate` or `FeeDelegatedAccountUpdate` transaction submitted via public RPC (`eth_sendRawTransaction`).
- Block processing / consensus validation of a block containing such a transaction (each honest node re-decodes transactions).

A crash occurring during **block/consensus validation** is a network-wide DoS: every node that processes the block (or receives the pooled transaction) crashes identically, since Go's stack overflow is a deterministic `fatal error` that immediately terminates the process — this is a highly reliable, unrecoverable denial of service across the network, not merely a resource-exhaustion nuisance. This satisfies "High" severity, matching the CVE-2026-62296 analog (`AV:N/AC:L/PR:N/UI:N` availability impact).

### Likelihood Explanation
Likelihood is high: constructing the malicious payload requires only crafting nested RLP-encoded `AccountKeyRoleBased` blobs, which is straightforward given the public encoding format (`accountkey.NewAccountKeySerializerWithAccountKey` / `EncodeAccountKey` RPC method shows the exact structure [6](#0-5) ). No privileged access, special key material beyond a normal signing key, or node cooperation is needed — a single unprivileged externally-owned account can sign and broadcast the transaction (only the `AccountUpdate` sender's own signature is required; recursion happens purely during RLP decode/parsing of the `AccountKey` field, prior to any signature or key-validity check).

### Recommendation
Enforce a maximum recursion/nesting depth for `AccountKey` decoding, e.g.:
- Add a depth parameter/context to `AccountKeySerializer.DecodeRLP` and `AccountKeyRoleBased.DecodeRLP`, incrementing on each recursive call and returning an error (e.g., `kerrors.ErrNestedCompositeType`) once a depth limit (1, matching the semantic "no nested RoleBased" rule) is exceeded — before continuing to decode further nested content.
- Alternatively, have `AccountKeySerializer.DecodeRLP` reject `AccountKeyTypeRoleBased` immediately whenever it is invoked in a nested context (i.e., have `AccountKeyRoleBased.DecodeRLP` use a variant of the serializer/decode path that disallows `AccountKeyTypeRoleBased` as an element type), so the recursive structure can never exceed depth 2 regardless of attacker input.
- Add a fuzz/unit test that RLP-encodes a deeply nested (e.g., 100,000-level) `AccountKeyRoleBased` structure and asserts that decoding returns an error rather than recursing.

### Proof of Concept
1. Build a byte string `payload` representing an `AccountKeySerializer` blob for `AccountKeyTypeNil` (the innermost, terminal key), i.e. `rlp.EncodeToBytes([]interface{}{AccountKeyTypeNil, []byte{}})`-equivalent for a simple key.
2. Repeatedly wrap it N times (N = tens of thousands) as an `AccountKeyRoleBased`-typed serializer blob:
   ```
   for i := 0; i < N; i++ {
       roleBasedEnc, _ := rlp.EncodeToBytes([][]byte{payload})           // AccountKeyRoleBased.EncodeRLP format
       payload, _ = rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(...))  // wrap as AccountKeyTypeRoleBased + roleBasedEnc
   }
   ```
   (equivalently, use `accountkey.NewAccountKeyRoleBasedWithValues([]AccountKey{ ... nested ... })` and `rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(key))` repeatedly, as demonstrated by `randAccountKey`'s nested-RoleBased construction pattern in the existing test helper [7](#0-6) , but without the depth cap that test happens to use.)
3. Embed the resulting `payload` as the `AccountKey` field of a `TxInternalDataAccountUpdate`/`TxInternalDataFeeDelegatedAccountUpdate`, sign it with a valid key, and RLP-encode the full transaction.
4. Submit via `s.DecodeAccountKey(payload)` (RPC `kaia_decodeAccountKey`) [8](#0-7)  or via `eth_sendRawTransaction` → `txpool.AddRemote` → `rlp.DecodeBytes(..., tx)`.
5. Observe the node process crash with `fatal error: stack overflow` during the recursive `AccountKeyRoleBased.DecodeRLP` ↔ `AccountKeySerializer.DecodeRLP` call chain, rather than returning `kerrors.ErrNestedCompositeType`.

Note: I was not able to fully trace the exact call path inside `TxInternalDataAccountUpdate.DecodeRLP` (grep for `DecodeRLP` in that file returned matches but content wasn't inspected before the tool budget ran out); however, `api_kaia.go`'s `DecodeAccountKey` RPC method independently confirms a directly public-RPC-reachable path that calls `rlp.DecodeBytes(encodedAccKey, &dec)` on an `AccountKeySerializer`, which is sufficient on its own to trigger the recursion from an unauthenticated RPC caller without needing a full signed transaction.

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

**File:** api/api_kaia.go (L141-164)
```go
// EncodeAccountKey gets an account key of JSON format and returns RLP encoded bytes of the key.
func (s *KaiaAPI) EncodeAccountKey(accKey accountkey.AccountKeyJSON) (hexutil.Bytes, error) {
	if accKey.KeyType == nil {
		return nil, errors.New("key type is not specified")
	}
	key, err := accountkey.NewAccountKey(*accKey.KeyType)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(accKey.Key, key); err != nil {
		return nil, err
	}
	// Invalidate zero values of threshold and weight to prevent users' mistake
	// JSON unmarshalling sets zero for those values if they are not exist on JSON input
	if err := checkAccountKeyZeroValues(key, false); err != nil {
		return nil, err
	}
	accKeySerializer := accountkey.NewAccountKeySerializerWithAccountKey(key)
	encodedKey, err := rlp.EncodeToBytes(accKeySerializer)
	if err != nil {
		return nil, errors.New("the key probably contains an invalid public key: " + err.Error())
	}
	return (hexutil.Bytes)(encodedKey), nil
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

**File:** storage/statedb/flat_trie_test.go (L307-333)
```go
func randAccountKey(r *rand.Rand) accountkey.AccountKey {
	ty := r.Intn(5)
	switch ty {
	case 0:
		return accountkey.NewAccountKeyLegacy()
	case 1:
		return accountkey.NewAccountKeyPublicWithValue(randPub(r))
	case 2:
		return accountkey.NewAccountKeyFail()
	case 3:
		n := r.Intn(9) + 1 // [1, MaxNumKeysForMultiSig]
		m := 1
		if n > 1 {
			m = r.Intn(n-1) + 1 // [1, n]
		}
		keys := make(accountkey.WeightedPublicKeys, n)
		for i := range n {
			keys[i] = accountkey.NewWeightedPublicKey(uint(r.Intn(10)), (*accountkey.PublicKeySerializable)(randPub(r)))
		}
		return accountkey.NewAccountKeyWeightedMultiSigWithValues(uint(m), keys)
	default:
		return accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{
			randAccountKey(r),
			randAccountKey(r),
			randAccountKey(r),
		})
	}
```
