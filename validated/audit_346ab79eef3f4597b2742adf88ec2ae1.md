### Title
Unbounded recursive AccountKey decoding causes stack-overflow DoS via TxTypeAccountUpdate (and DecodeAccountKey RPC) - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
The reported iccDEV bug class is unbounded recursion in a calculator/parser (`SIccCalcOp::ArgsUsed()`) that overflows the native stack when processing an attacker-supplied, deeply nested structure. Kaia's `accountkey` RLP decoding path exhibits the same bug class: `AccountKeyRoleBased.DecodeRLP` recursively invokes `rlp.DecodeBytes` on each sub-key blob, and each sub-key can itself be another `AccountKeyRoleBased`-typed blob, producing unbounded Go call-stack recursion driven entirely by attacker-controlled input, with no depth check performed before recursing.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte-strings and, for every element, calls `rlp.DecodeBytes(b, &serializer)` where `serializer` is an `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType` and then decodes the actual key value into it via `s.Decode(serializer.key)`: [2](#0-1) 

If `keyType` is `AccountKeyTypeRoleBased` again, `s.Decode(serializer.key)` re-enters `AccountKeyRoleBased.DecodeRLP`, which again iterates over its sub-elements and calls `rlp.DecodeBytes` recursively. There is **no depth limit or nesting check performed during decode** — the only place nested `RoleBased` keys are rejected is `CheckInstallable`, which runs only *after* decoding succeeds: [3](#0-2) 

This means a crafted RLP blob consisting of many nested `AccountKeyRoleBased` wrappers (each level adding only a few bytes of overhead) can drive the Go call stack to overflow before the composite-type check ever executes.

This decode path is reachable in two ways relevant to an unprivileged actor:
1. `TxInternalDataAccountUpdate.fromSerializable` (used when a `TxTypeAccountUpdate` transaction is deserialized from RLP, e.g. read from the wire or the tx pool) calls `rlp.DecodeBytes(serialized.Key, serializer)` on the `Key` field taken directly from the raw transaction bytes, with no size/nesting validation prior to decoding: [4](#0-3) 
2. The public JSON-RPC method `KaiaAPI.DecodeAccountKey` accepts arbitrary attacker-supplied bytes and feeds them straight into `rlp.DecodeBytes`: [5](#0-4) 

### Impact Explanation
A crash in `rlp.DecodeBytes`/`AccountKeyRoleBased.DecodeRLP` due to stack overflow is a fatal, unrecoverable Go runtime crash (Go stack overflows abort the process — they cannot be caught by `recover()`). Since transaction decoding happens uniformly on every node that processes the p2p-broadcast transaction or includes it in a block, a single malicious `TxTypeAccountUpdate`-style transaction (or fee-delegated variant) submitted by any unprivileged sender could crash all nodes that decode it, i.e. a network-wide denial of service. Equally, any public RPC endpoint exposing `kaia_decodeAccountKey` can be crashed by an unauthenticated caller supplying a crafted byte string, taking down that RPC node process.

### Likelihood Explanation
High reachability: constructing the malicious payload requires no special privileges, only crafting nested byte strings that resemble RLP-encoded `AccountKeySerializer` values with `keyType = AccountKeyTypeRoleBased`. Because each nesting level only costs a few bytes of RLP overhead, thousands of nesting levels (enough to exhaust a typical several-MB goroutine stack) can fit well within ordinary transaction-size limits. No cryptographic signature validity is needed to trigger the crash during decode: RLP decoding happens before/independently of signature verification in the transaction ingestion path.

### Recommendation
- Add an explicit recursion/nesting-depth counter (e.g., via a `depth` argument threaded through `AccountKeySerializer.DecodeRLP`/`AccountKeyRoleBased.DecodeRLP`, or a `Stream`-level nesting counter) and reject decoding once a small maximum depth (e.g., 1, since nested RoleBased keys are never valid) is exceeded — mirroring the existing `CheckInstallable`/`ErrNestedCompositeType` semantics but enforced *during* decode, not after.
- Alternatively, reject `AccountKeyTypeRoleBased` values found while already decoding within a `RoleBased` context, immediately inside `AccountKeySerializer.DecodeRLP`, before recursing into `s.Decode(serializer.key)`.
- Apply the same guard to the JSON `UnmarshalJSON` path for consistency, and add a fuzz/regression test asserting that deeply nested encoded account keys return an error rather than crashing.

### Proof of Concept
Conceptually (exact byte-level construction would be validated by a Devin agent with code execution):
1. Build an `AccountKeyLegacy`-typed base key `K0` and RLP-encode it via `AccountKeySerializer`.
2. Wrap `K0` in an `AccountKeyRoleBased` with itself as its only sub-key, RLP-encode that as `K1` (`AccountKeyRoleBased.EncodeRLP` just RLP-encodes `[][]byte` of each sub-key's serialized bytes) — see `EncodeRLP`: [6](#0-5) 
3. Repeat step 2 thousands of times, each time wrapping the previous serialized blob as the sole sub-key of a new `AccountKeyRoleBased`, producing a byte string `K_n`.
4. Call `kaia_decodeAccountKey(K_n)` via RPC, or submit a `TxTypeAccountUpdate` transaction whose `Key` field is `K_n`.
5. Observe the node process crash with a Go stack-overflow fatal error inside `AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` recursion, before `CheckInstallable`'s `ErrNestedCompositeType` check is ever reached.

### Citations

**File:** blockchain/types/accountkey/account_key_role_based.go (L110-118)
```go
func (a *AccountKeyRoleBased) EncodeRLP(w io.Writer) error {
	enc := make([][]byte, len(*a))

	for i, k := range *a {
		enc[i], _ = rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(k))
	}

	return rlp.Encode(w, enc)
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
