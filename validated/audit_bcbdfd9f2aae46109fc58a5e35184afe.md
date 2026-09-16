### Title
Uncontrolled recursion in `AccountKeyRoleBased.DecodeRLP` allows unauthenticated stack-overflow DoS via nested RoleBased AccountKey RLP - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each of its member keys through `AccountKeySerializer`, and if a member key is itself an `AccountKeyRoleBased`, decoding recurses again with no depth limit. Because nested composite keys are only rejected *after* successful decoding (in `CheckInstallable`), an attacker can submit RLP-encoded data with thousands of nested `AccountKeyRoleBased` levels to crash any node that decodes it — reachable from a single raw transaction or from the public RPC method that decodes an account key directly.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte-strings, then for every element constructs a fresh `AccountKeySerializer` and calls `rlp.DecodeBytes(b, &serializer)`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads the key type, allocates a concrete `AccountKey` via `NewAccountKey`, and decodes into it: [2](#0-1) 

If the decoded `keyType` is `AccountKeyTypeRoleBased`, `serializer.key` is an `*AccountKeyRoleBased`, so `s.Decode(serializer.key)` calls `AccountKeyRoleBased.DecodeRLP` again — recursing into the same code path with no segment/depth cap, exactly analogous to `mapHas`'s unbounded per-segment recursion in the node-tar advisory (there: `path.dirname()` per `/`; here: one recursive `DecodeRLP` call per nesting level of `AccountKeyRoleBased`).

The only guard against nested composite keys is `IsCompositeType()` inside `CheckInstallable`, which runs *after* decoding has already completed: [3](#0-2) 

This mirrors the report's root cause pattern precisely: the depth/validation guard (`maxDepth` in `[CHECKPATH]` for node-tar; `IsCompositeType`/`CheckInstallable` here) executes on a later event than the vulnerable recursive decode, so the stack overflow occurs before any guard can fire.

Reachability:
- Every RLP-decoded `TxTypeAccountUpdate` transaction decodes its `AccountKey` field through this same path (`blockchain/types/transaction.go:240-254` `DecodeRLP` → internal-data serializer → AccountKey decode), so a crafted raw transaction submitted via `eth_sendRawTransaction`/p2p tx propagation reaches the recursive decoder before any signature or composite-type check.
- The public RPC method `DecodeAccountKey` decodes attacker-supplied bytes directly with no transaction wrapper at all: [4](#0-3) 

Go's runtime stack-overflow is a fatal, unrecoverable error (`fatal error: stack overflow`) that terminates the process — it cannot be caught by `recover()`, so no defensive code in the RPC handler or tx-pool pipeline can stop the crash.

### Impact Explanation
Any unauthenticated caller of the public RPC (`kaia_decodeAccountKey`) or any peer submitting a single crafted raw transaction can crash a full/consensus node's process with a tiny payload (RLP nesting is compact — each extra level costs only a few bytes of list/type overhead). This is a remote, unauthenticated denial-of-service against public-RPC nodes and any node that must decode incoming transactions (mempool admission, block validation), potentially affecting network availability and liveness — matching High severity DoS impact (CWE-400/674), consistent with the reported bug class.

### Likelihood Explanation
High. No authentication, signature validity, or balance is required to reach `DecodeAccountKey` via RPC. For the transaction path, RLP decoding of the `AccountKey` field happens prior to signature verification and prior to the `IsCompositeType`/`CheckInstallable` checks, so a single crafted byte blob is sufficient; no special network conditions or race are needed.

### Recommendation
- Add an explicit recursion/nesting-depth limit to `AccountKeyRoleBased.DecodeRLP` (and to `AccountKeySerializer.DecodeRLP`) — reject decoding once nesting exceeds a small constant (e.g., 1, since nested RoleBased keys are never valid) *before* recursing, not after decoding completes.
- Alternatively, disallow decoding a `AccountKeyTypeRoleBased` key type as a member of another `AccountKeyRoleBased` at the point `NewAccountKey`/`DecodeRLP` is invoked recursively, so the recursion never proceeds past depth 1.
- Apply the same rejection to the RPC `DecodeAccountKey`/`EncodeAccountKey` entry points, since they bypass the tx-pool pipeline entirely.

### Proof of Concept
```go
// Build nested AccountKeyRoleBased RLP: level N wraps level N-1 as its single sub-key.
// AccountKeyRoleBased is RLP-encoded as: rlp.Encode([][]byte{ rlp.EncodeToBytes(AccountKeySerializer{keyType, subKey}) })
// Each level only adds ~keyType byte + list/string RLP overhead (a few bytes),
// so ~50,000+ nesting levels fit comfortably within normal tx-size limits.

innermost := accountkey.NewAccountKeyNil()
var cur accountkey.AccountKey = innermost
for i := 0; i < 50000; i++ {
    cur = accountkey.NewAccountKeyRoleBasedWithValues([]accountkey.AccountKey{cur})
}
encoded, _ := rlp.EncodeToBytes(accountkey.NewAccountKeySerializerWithAccountKey(cur))

// Attack vector 1: public RPC
// kaia.DecodeAccountKey(encoded) -> recurses AccountKeyRoleBased.DecodeRLP 50000 times -> fatal stack overflow, process exits.

// Attack vector 2: raw transaction
// Embed `encoded` as the AccountKey field of a TxTypeAccountUpdate transaction and submit via eth_sendRawTransaction;
// tx.DecodeRLP triggers the same unbounded recursion before signature checks run.
```

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
