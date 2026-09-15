### Title
Uncontrolled recursion in `AccountKeyRoleBased` RLP decoding allows stack-overflow DoS via nested role-based account keys - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each sub-key by calling `rlp.DecodeBytes` on an `AccountKeySerializer`, which itself may again be typed `AccountKeyTypeRoleBased`, re-entering `AccountKeyRoleBased.DecodeRLP`. No depth limit exists anywhere in the decode path, so an attacker-controlled, deeply nested account key blob causes unbounded Go-stack recursion during RLP decoding, analogous to the uncontrolled recursion in CVE-2021-27432.

### Finding Description
`AccountKeySerializer.DecodeRLP` reads a `keyType` and then calls `s.Decode(serializer.key)`, dispatching to the concrete `AccountKey` implementation's `DecodeRLP`: [1](#0-0) 

When `keyType` is `AccountKeyTypeRoleBased`, this reaches `AccountKeyRoleBased.DecodeRLP`, which iterates over the encoded sub-key byte strings and, for each one, calls `rlp.DecodeBytes(b, &serializer)` on a fresh `AccountKeySerializer` — recursively re-entering the same decode path with no nesting-depth check: [2](#0-1) 

The only guard against nested composite keys (`IsCompositeType()` / `kerrors.ErrNestedCompositeType`) is enforced in `CheckInstallable` and `CheckUpdatable`, which run **after** decoding has already completed: [3](#0-2) 

Because the recursion happens during `DecodeRLP` itself — before `CheckInstallable`/`CheckUpdatable` ever execute — an attacker can construct a byte blob encoding many levels of `AccountKeyRoleBased{ AccountKeyRoleBased{ AccountKeyRoleBased{ ... } } }` and the Go call stack will grow proportionally to nesting depth with no cap, independent of the later semantic validation that only rejects a *single* level of nesting.

This decode path is reachable from at least two unprivileged, unauthenticated entry points:
1. The public RPC method `DecodeAccountKey`, which takes arbitrary user-supplied bytes and calls `rlp.DecodeBytes` directly on an `AccountKeySerializer`: [4](#0-3) 
2. A `TxTypeAccountUpdate` transaction submitted by any account (`eth_sendRawTransaction`/`SendTransaction`), whose account-key field is decoded the same way when the transaction is RLP-decoded from the wire, prior to any pool admission or `CheckInstallable` validation.

### Impact Explanation
Uncontrolled recursion driving unbounded stack growth in Go typically manifests as a fatal runtime stack-overflow, which terminates the process (Go does not allow recovering from a stack overflow via `recover()`). Because this is reachable via a public JSON-RPC call (`kaia_decodeAccountKey`) and via ordinary transaction decoding, a single unprivileged caller can crash a full/RPC node without needing funds, special permissions, or committing anything on-chain. This matches the "High" severity of the underlying CVE-2021-27432: a remotely triggerable stack-overflow DoS.

### Likelihood Explanation
Likelihood is high: no authentication, balance, or on-chain state is required. The `DecodeAccountKey` RPC endpoint is typically exposed to any RPC client, and account-update transactions are attacker-constructible off-chain and only need to reach the RLP decoder (which happens before any semantic nested-key check). Crafting deeply nested RLP is straightforward and cheap.

### Recommendation
Add an explicit nesting-depth counter/limit that is enforced *during* `AccountKeyRoleBased.DecodeRLP` (and generally in `AccountKeySerializer.DecodeRLP`), rejecting any account key whose composite nesting exceeds 1 level (matching the semantic rule already enforced by `CheckInstallable`/`IsCompositeType`) before recursing into `rlp.DecodeBytes`. Alternatively, thread a depth parameter through the decode call chain (e.g., via a package-level recursion guard or a wrapping decoder that tracks depth in the `rlp.Stream`) and return an error such as `kerrors.ErrNestedCompositeType` as soon as depth exceeds the allowed maximum, rather than only checking after full decode completes.

### Proof of Concept
Conceptually (cannot execute in this environment — logic derived from source review):
1. Construct nested `AccountKeyRoleBased` values: `k1 = RoleBased([Public])`, `k2 = RoleBased([k1])`, `k3 = RoleBased([k2])`, ... repeated N times (N large, e.g., tens of thousands), each encoded via `AccountKeyRoleBased.EncodeRLP`/`AccountKeySerializer.EncodeRLP` (this can be done without going through `CheckInstallable`, e.g. by directly RLP-encoding the Go structures rather than using the validated key-creation API).
2. Submit the resulting bytes to the public `kaia_decodeAccountKey` RPC method, or embed it as the `AccountKey` field of a `TxTypeAccountUpdate` transaction and submit via `eth_sendRawTransaction`.
3. On decode, `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recurses N times before any `CheckInstallable`/nested-type check occurs, exhausting the goroutine stack and crashing the node process. [2](#0-1) [4](#0-3)

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
