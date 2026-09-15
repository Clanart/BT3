### Title
Unbounded recursion during RLP decoding of nested `AccountKeyRoleBased` in account-update transactions - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
`TxTypeAccountUpdate` (and its fee-delegated variants) transactions carry an `accountkey.AccountKey` field that is RLP-decoded via `AccountKeySerializer.DecodeRLP`. When the encoded key type is `AccountKeyTypeRoleBased`, decoding recurses into `AccountKeyRoleBased.DecodeRLP`, which itself decodes each list element as a fresh `AccountKeySerializer` that can again be typed `AccountKeyTypeRoleBased`. This recursive decode happens purely from attacker-controlled RLP bytes and completes *before* any composite-type validation (`CheckInstallable`/`CheckUpdatable`, which reject nested role-based keys) is ever invoked. A transaction whose `Key` field is deeply/self-nested `AccountKeyRoleBased` structures can drive unbounded Go-stack recursion during decode, mirroring the PDF outline-tree `StackOverflowError` bug class (CVE-2026-47851).

### Finding Description
The decode chain is:
- `TxInternalDataAccountUpdate.DecodeRLP` → `fromSerializable` → `rlp.DecodeBytes(serialized.Key, serializer)` [1](#0-0) 
- `AccountKeySerializer.DecodeRLP` reads the `keyType`, constructs the concrete key via `NewAccountKey`, then decodes into it with `s.Decode(serializer.key)` [2](#0-1) 
- If `keyType == AccountKeyTypeRoleBased`, `NewAccountKey` returns an `AccountKeyRoleBased` [3](#0-2) 
- `AccountKeyRoleBased.DecodeRLP` decodes a list of byte-strings and, for **each** element, again calls `rlp.DecodeBytes(b, &serializer)` into a brand-new `AccountKeySerializer` — which can itself be `AccountKeyTypeRoleBased`, re-entering the same recursive path [4](#0-3) 

Critically, the safeguard that exists in the codebase — rejecting a `RoleBasedKey` that contains another `RoleBasedKey` as a role member — is only enforced *after* the full structure is decoded, inside `CheckInstallable`/`CheckUpdatable` (`IsCompositeType()` check) [5](#0-4)  and in the JSON-only helper `checkAccountKeyZeroValues` used by the `kaia_encodeAccountKey` RPC [6](#0-5) . There is no recursion-depth or nesting-depth limit enforced *during* RLP decoding itself, and the `rlp.Stream` decoder has no generic recursion-depth guard for `Decoder`-implementing types [7](#0-6) . A test even documents that nested role-based keys are rejected only functionally (post-decode), not structurally during decode: `TestAccountUpdateRoleBasedKeyNested` shows the rejection happening at `txpool.AddRemote`/block validation time via `kerrors.ErrNestedCompositeType`, i.e., after the tx (and therefore its key) has already been fully RLP-decoded [8](#0-7) .

Because the decoding recursion depth is driven directly by how many times an attacker nests `AccountKeyRoleBased` byte-strings inside one another, and each nesting level only costs a handful of RLP header bytes, an attacker can construct a `TxTypeAccountUpdate` transaction whose `Key` field decodes through tens of thousands of recursive Go function calls (`DecodeRLP` → `rlp.DecodeBytes` → `Stream.Decode` → `decodeDecoder` → …), exhausting the goroutine stack and crashing the node with a stack-overflow fatal error.

### Impact Explanation
Any unprivileged party can submit such a transaction via the public RPC (`eth_sendRawTransaction`/`klay_sendRawTransaction`) or gossip it to peers; it is processed by `blockchain.TxPool` during RLP decoding, before signature/role validation. A crash here brings down the node's transaction-processing goroutine/process — a remote, unauthenticated denial-of-service against consensus/RPC-serving nodes, matching the CVSS vector's `A:H` with `AV:N/AC:L/PR:N/UI:N`.

### Likelihood Explanation
High. No special privileges, prior transactions, or funded accounts are required; the encoded key blob itself never needs to be cryptographically valid because the crash occurs during decoding, prior to any signature or key validation. Constructing deeply nested `AccountKeyRoleBased` RLP bytes is straightforward and cheap relative to the achievable recursion depth.

### Recommendation
Enforce an explicit maximum nesting/recursion depth check when decoding `AccountKeyRoleBased` (and generally for any recursive `AccountKey` composite decode), rejecting inputs with `IsCompositeType()` structural nesting beyond a small fixed depth (Kaia's own semantics already disallow nesting a `RoleBasedKey` inside a `RoleBasedKey`, so depth should conceptually be limited to 1). This check must be applied incrementally during `AccountKeyRoleBased.DecodeRLP` (or a wrapping decode-time depth counter passed through `rlp.Stream`), not only in the post-decode `CheckInstallable`/`CheckUpdatable` path, so that a single decode call cannot recurse without bound before validation ever runs.

### Proof of Concept
1. Construct a byte string `k0 = RLP(AccountKeySerializer{ keyType: RoleBased, key: RLP([]) })` — an empty-list `AccountKeyRoleBased`.
2. Iteratively build `k_{i+1} = RLP(AccountKeySerializer{ keyType: RoleBased, key: RLP([k_i]) })`, nesting a single-element role-based key inside the previous one, repeated N times (e.g., N = 50,000–200,000, achievable in a payload of a few hundred KB to a few MB depending on stack-frame size headroom).
3. Set this `k_N` bytes as the `Key` field of a `TxTypeAccountUpdate` transaction (`TxInternalDataAccountUpdate.Key`), sign it with any valid key for the `From` account (or submit unsigned/pool-relevant to trigger decode-time crash if pool decode occurs before signature check), and submit via `eth_sendRawTransaction`.
4. `TxInternalDataAccountUpdate.DecodeRLP` → `fromSerializable` → recursive `AccountKeyRoleBased.DecodeRLP` chain executes N levels deep, exhausting the goroutine stack and crashing the node process with a fatal stack overflow before `CheckInstallable`/`CheckReplacable` composite-type rejection is ever reached.

Note: exact N required to trigger a crash (dependent on Go runtime max-stack settings and per-frame size introduced by `reflect`-based RLP decoding) could not be empirically measured in this analysis; only static code-path/reachability was verified, so the depth threshold should be confirmed by an engineer with a running/instrumented node.

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

**File:** rlp/decode.go (L544-546)
```go
func decodeDecoder(s *Stream, val reflect.Value) error {
	return val.Addr().Interface().(Decoder).DecodeRLP(s)
}
```

**File:** tests/account_keytype_test.go (L1811-1822)
```go
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
