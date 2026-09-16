### Title
Unbounded recursion decoding nested `AccountKeyRoleBased` from a single RLP-encoded transaction can crash a node - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
Kaia allows an unprivileged sender to submit an `AccountUpdate` transaction whose `Key` field encodes an `AccountKeyRoleBased` value. Decoding this field is fully recursive with no depth check, mirroring the jq `jv_dump_term` bug class in CVE-2016-4074 (unbounded recursion over attacker-controlled nested data leading to stack exhaustion and crash), except here the unbounded recursion happens during **decoding** of a transaction field rather than dumping JSON.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes its payload into a `[][]byte` and then, for every element, calls `rlp.DecodeBytes` into a fresh `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type and then recursively decodes into the concrete key implementation via `s.Decode(serializer.key)`: [2](#0-1) 

If the concrete key type is again `AccountKeyTypeRoleBased`, this dispatches straight back into `AccountKeyRoleBased.DecodeRLP`, and the cycle repeats for however many nesting levels are present in the attacker-supplied byte string. There is no depth counter or recursion limit anywhere in this call chain (`DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey` → `s.Decode` → back to `AccountKeyRoleBased.DecodeRLP`).

The only defense against nested `RoleBased` keys, `IsCompositeType`/`CheckInstallable` (enforced via `ErrNestedCompositeType` as demonstrated in `TestAccountUpdateRoleBasedKeyNested`), is applied **after** decoding succeeds — it validates the already-decoded structure, it does not bound the depth of the decode itself: [3](#0-2) 

Because each nesting level requires only a handful of RLP bytes (a `[][]byte` wrapper plus a 1-byte key-type tag), an attacker can pack many thousands of nesting levels into a transaction that is still well under typical transaction-size limits, driving the recursive decode chain to a very large depth.

### Impact Explanation
Each recursion level invokes multiple Go function calls with non-trivial stack frames (`DecodeRLP`, `rlp.DecodeBytes`, `NewStream`, `Stream.Decode`, `AccountKeySerializer.DecodeRLP`, `NewAccountKey`). Sufficiently deep nesting can drive the goroutine stack past Go's default maximum stack size, which triggers an unrecoverable `fatal error: goroutine stack exceeds ... limit` that crashes the entire node process (not just the request), since Go does not allow `recover()` from this class of fatal error. This is reachable from:
- `eth_sendRawTransaction` / p2p tx propagation (`TxPool.AddRemote` → RLP decode of the transaction, including nested `AccountUpdate.Key`),
- `kaia_decodeAccountKey` RPC (`DecodeAccountKey`) which explicitly performs `rlp.DecodeBytes(encodedAccKey, &dec)` on user input: [4](#0-3) 

A successful crash denies service to the whole node (validator or RPC node), which is a High-severity availability impact consistent with the CVE-2016-4074 analog (stack consumption → application crash from a single crafted input).

### Likelihood Explanation
The attack requires only crafting a single RLP payload and either submitting it as a transaction or calling the public `kaia_decodeAccountKey` RPC method — no special privileges, staking, or governance access needed. The `ErrNestedCompositeType` check exists specifically to reject nested `RoleBased` keys, indicating this bug class was anticipated for shallow (1-level) nesting, but it evidently does not prevent the underlying recursive-decode from running to arbitrary depth before that check is ever reached.

### Recommendation
- Add an explicit recursion/depth counter (or reuse the RLP `Stream`'s own list-depth tracking) to `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, and reject decoding once a small maximum depth (e.g., 1, since nested role-based keys are already disallowed) is exceeded — fail fast on the *first* byte string check rather than after full recursive decode.
- Alternatively, restructure decoding so that `AccountKeySerializer.DecodeRLP` rejects `AccountKeyTypeRoleBased` immediately when invoked in a nested context (i.e., have `AccountKeyRoleBased.DecodeRLP` peek the key-type byte of each sub-key and reject `RoleBased` before recursing), matching the existing `isNested` semantics used in `checkAccountKeyZeroValues`.
- Apply the same fix to the RPC-reachable `KaiaAPI.DecodeAccountKey` path in `api/api_kaia.go`, since it directly exposes the vulnerable decoder to arbitrary caller input.

### Proof of Concept
Conceptual construction (exact byte encoding to be validated by the implementer/background agent, since the index does not provide a runnable RLP fuzz harness here):
1. Build an `AccountKeySerializer` encoding for `AccountKeyTypeRoleBased` whose single "role key" element is itself an `AccountKeySerializer` encoding for `AccountKeyTypeRoleBased`, and repeat this nesting N times (N large, e.g. 50,000–200,000), each level adding only a few bytes of RLP list/string framing.
2. Wrap this into an `AccountUpdate` transaction's `Key` field (`hexutil.Bytes`), sign it, and submit via `eth_sendRawTransaction`, or pass the raw nested-encoded bytes directly to `kaia_decodeAccountKey`.
3. On decode, `AccountKeyRoleBased.DecodeRLP` recurses N times before any `ErrNestedCompositeType` check is reached, exhausting the goroutine stack and crashing the node process with a fatal, non-recoverable Go runtime error.

Note: I was not able to find in the index an explicit maximum RLP nesting-depth guard or global recursion limit applied ahead of `AccountKeyRoleBased.DecodeRLP`/`AccountKeySerializer.DecodeRLP`, nor could I verify the exact transaction-size cap enforced by `tx_pool.go` from the available search results (matches were found in `blockchain/tx_pool.go` and `blockchain/error.go` for size-related constants, but their exact values were not retrieved). A background Devin session with full repository access would be needed to confirm the precise transaction size limit and to build/run a concrete PoC transaction that demonstrates the crash end-to-end.

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
