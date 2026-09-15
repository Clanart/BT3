## Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` keys allows remote stack-overflow DoS via a single transaction - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively re-invokes `rlp.DecodeBytes` on each embedded key blob through `AccountKeySerializer.DecodeRLP`, and neither function enforces any nesting-depth limit. An attacker who submits a single `TxTypeAccountUpdate`/`TxTypeAccountCreation` (or fee-delegated variant) transaction whose account-key field contains a `AccountKeyRoleBased` value nested many levels deep (RoleBased → serialized key blob → RoleBased → …) forces the node to recurse through Go call stacks with essentially no limit before any semantic validation (`CheckInstallable`, nested-composite-type rejection) ever runs. This is analogous to the Parse Server issue where deeply nested query condition operators crashed the server before any complexity check could reject the request.

### Finding Description
`AccountKeySerializer.DecodeRLP` decodes the key type, allocates a matching `AccountKey`, and calls `s.Decode(serializer.key)`: [1](#0-0) 

When the declared type is `AccountKeyTypeRoleBased`, decoding dispatches to `AccountKeyRoleBased.DecodeRLP`, which decodes a list of raw byte blobs and, for **each** blob, calls `rlp.DecodeBytes(b, &serializer)` — i.e., it re-enters `AccountKeySerializer.DecodeRLP` recursively: [2](#0-1) 

Because the sub-key type is itself attacker-controlled and can again be `AccountKeyTypeRoleBased`, this decode chain recurses arbitrarily deep with no depth counter, no recursion guard, and no early check that the inner key is non-composite. The only defense against nested `RoleBased` keys — the `IsCompositeType()`/`ErrNestedCompositeType` check in `CheckInstallable`/`CheckUpdatable` — runs *after* the entire RLP structure has already been fully decoded: [3](#0-2) 

Likewise the JSON-based path (`checkAccountKeyZeroValues` in `api_kaia.go`) only rejects nested `RoleBased` keys after `json.Unmarshal`/`AccountKeySerializer.UnmarshalJSON` has already recursed through the structure: [4](#0-3) [5](#0-4) 

The account-key blob is decoded as part of ordinary transaction deserialization for account-update/creation transaction types, e.g.: [6](#0-5) [7](#0-6) 

This decode step happens whenever a raw transaction is parsed — on p2p transaction propagation, `eth_sendRawTransaction`/`klay_sendRawTransaction` RPC calls, or block import — i.e., reachable from a single unprivileged transaction sender or public-RPC caller, before signature verification or `tx.Validate()`/`CheckInstallable()` gating is reached in `pool.validateTx` (`blockchain/tx_pool.go`).

Unlike Go's `json.Decoder`, which has a built-in nesting-recursion limit (as demonstrated by the JS tracer's "json encode recursion limit" test elsewhere in this codebase), the custom RLP decoder in `rlp/decode.go` (`makeStructDecoder`, `makeListDecoder`, `decodeDecoder`) has **no equivalent depth guard** — a search of the package confirms no `depth`/`maxDepth`/`recursion` counters exist anywhere in `rlp/decode.go`.

### Impact Explanation
Excessive recursion depth in Go causes uncontrolled goroutine stack growth. Once the per-goroutine maximum stack size is exceeded, the Go runtime raises a fatal, unrecoverable "stack overflow" error that terminates the entire process — `recover()` cannot catch it. Because transaction/account-key decoding is performed on the hot path for every incoming transaction (p2p gossip, RPC submission, and block processing), a single crafted transaction can crash the full node process, denying service to all users connected to that node and potentially affecting network-wide availability if propagated before signature checks reject it. This matches CWE-674 (Uncontrolled Recursion) and the High severity class of the reported analog (single unauthenticated/unprivileged request → full process crash).

### Likelihood Explanation
High likelihood: crafting the payload requires only nesting `AccountKeySerializer`/`AccountKeyRoleBased` RLP blobs, each level adding only a few bytes of overhead (key type byte + list header + serialized sub-blob), so a deeply nested structure fits well within normal transaction size limits while achieving thousands of recursion levels — more than sufficient to exhaust typical goroutine stack limits. No authentication, special privileges, staking, or governance access is required; the attacker just needs to submit one transaction of `TxTypeAccountUpdate` or `TxTypeAccountCreation` (or their fee-delegated variants) with a constructed `AccountKeyRoleBased` key field, or call `kaia_decodeAccountKey`/`kaia_encodeAccountKey` RPC endpoints directly.

### Recommendation
Add an explicit nesting-depth limit that is enforced *during* decoding rather than only after full construction:
- Introduce a `depth`/recursion counter (or a `Stream`-tracked list nesting depth similar to `requestComplexity.queryDepth` in the referenced patch) threaded through `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → recursive `rlp.DecodeBytes` calls, and reject decoding once a small fixed maximum (e.g., 1, since nested `RoleBased` is already logically disallowed) is exceeded.
- Alternatively, since a `RoleBased` key inside a `RoleBased` key is never semantically valid, perform a cheap non-recursive type-tag pre-check (peek at the encoded key type of each sub-key blob) before fully decoding it, and reject immediately if the sub-key type is `AccountKeyTypeRoleBased`, without recursing.
- Apply the equivalent check to the JSON decode path (`AccountKeySerializer.UnmarshalJSON` / `AccountKeyRoleBased.UnmarshalJSON`).
- Consider adding a general recursion/depth guard to the `rlp` package decoder for composite/list types, mirroring the "depth limit for query condition operator nesting" fix pattern from the referenced advisory.

### Proof of Concept
1. Construct nested `AccountKeySerializer` RLP blobs recursively, e.g. (pseudo-Go):
```go
inner := accountkey.NewAccountKeyPublicWithValue(pubKey) // base case
for i := 0; i < N; i++ { // N large, e.g. 50,000
    ser := accountkey.NewAccountKeySerializerWithAccountKey(
        accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{inner}),
    )
    encoded, _ := rlp.EncodeToBytes(ser)
    inner = /* wrap encoded bytes as the "key" of next level via a custom byte-blob composition matching AccountKeyRoleBased's [][]byte encoding */
}
```
   Concretely, since `AccountKeyRoleBased.EncodeRLP` encodes a `[][]byte` where each element is `rlp.EncodeToBytes(AccountKeySerializer)`, an attacker directly crafts the final RLP byte stream so that decoding one `AccountKeySerializer` triggers `AccountKeyRoleBased.DecodeRLP`, which calls `rlp.DecodeBytes` on a byte string that is itself an encoded `AccountKeySerializer` of type `RoleBased`, repeated N times.
2. Embed this crafted key blob as the `Key`/`KeyData` field of a `TxTypeAccountUpdate` (or `TxTypeAccountCreation`) transaction and RLP-encode/sign the transaction (signature validity is not required to reach the decode path since the account-key blob is decoded before signature verification during transaction deserialization).
3. Submit the raw transaction via `klay_sendRawTransaction`/`eth_sendRawTransaction` RPC or broadcast it over p2p to a node.
4. The node calls into `TxInternalDataAccountUpdate.fromSerializable` → `accountkey.NewAccountKeySerializer()` → `rlp.DecodeBytes(serialized.Key, serializer)`, which recurses N times through `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes`, exhausting the goroutine stack and crashing the node process with a fatal, unrecoverable Go runtime error.

*(Note: exact achievable recursion depth versus configured max transaction size and Go's default max stack size was not empirically measured in this analysis; the codebase and dependency versions were inspected via the indexed context only, so byte-per-level overhead and platform-specific stack limits should be verified in a live/test environment to confirm the exact N required to trigger the crash.)*

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

**File:** api/api_kaia.go (L175-197)
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

**File:** blockchain/types/tx_internal_data_account_creation.go (L179-196)
```go
func (t *TxInternalDataAccountCreation) fromSerializable(serialized *txInternalDataAccountCreationSerializable) error {
	t.AccountNonce = serialized.AccountNonce
	t.Price = serialized.Price
	t.GasLimit = serialized.GasLimit
	t.Recipient = serialized.Recipient
	t.Amount = serialized.Amount
	t.From = serialized.From
	t.HumanReadable = serialized.HumanReadable
	t.TxSignatures = serialized.TxSignatures

	serializer := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(serialized.KeyData, serializer); err != nil {
		return err
	}
	t.Key = serializer.GetKey()

	return nil
}
```
