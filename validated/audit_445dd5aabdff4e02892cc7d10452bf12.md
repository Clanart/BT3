## Analysis

The bug class in the report is **unbounded recursive expansion of a self-referential/nested structure during input processing, causing a stack overflow (CWE-674) reachable from a single crafted input**. The strongest reachable analog in this repo is the RLP decoding of `AccountKeyRoleBased`.

`AccountKeyRoleBased.DecodeRLP` decodes a list of opaque byte blobs and, for **each** element, constructs a fresh `AccountKeySerializer` and calls `rlp.DecodeBytes` on it, whose own `DecodeRLP` reads a key type and dispatches back into `NewAccountKey` → `s.Decode(serializer.key)`. If the decoded `keyType` is again `AccountKeyTypeRoleBased`, this decoding path recurses into `AccountKeyRoleBased.DecodeRLP` again, with **no depth counter or recursion-depth check anywhere in this call chain**: [1](#0-0) [2](#0-1) [3](#0-2) 

The only defense against nested `AccountKeyRoleBased` is `IsCompositeType()` combined with `CheckInstallable`/`CheckUpdatable`, but those checks only inspect **one level** of nesting (the immediate child keys of a role-based key) and are applied **after** the entire structure has already been fully RLP-decoded into memory/stack frames: [4](#0-3) [5](#0-4) 

This decode path is reachable by an unprivileged actor in two ways:
1. Submitting a `TxTypeAccountUpdate` transaction whose `AccountKey` field is a maliciously deeply-nested RLP blob (decoded via `TxInternalDataAccountUpdate`'s RLP decode, which embeds an `AccountKeySerializer`/`AccountKeyRoleBased`).
2. Calling the public RPC method `kaia_decodeAccountKey`, which directly performs `rlp.DecodeBytes(encodedAccKey, &dec)` on `AccountKeySerializer` with attacker-supplied bytes, with no size/depth pre-validation before decoding: [6](#0-5) 

Because the RLP `Stream`/decoder framework itself imposes no recursive-descent depth limit (only byte-length limits via `NewStream(r, limit)`), an attacker can encode a chain of nested `AccountKeyRoleBased` → `AccountKeyRoleBased` → ... blobs that is small in total size but deep in nesting, driving unbounded Go-stack recursion through `DecodeRLP` → `rlp.DecodeBytes` → `DecodeRLP` → ... until the goroutine stack overflows the process, crashing/DoS'ing the node — directly analogous to the iccDEV XML macro-expansion stack overflow (recursive expansion with no depth bound).

I could not fully verify within the tool-call budget whether `TxInternalDataAccountUpdate`'s own RLP decode path independently invokes `CheckInstallable` *before* the nested decode completes (which would still not help, since the crash occurs during decode itself, before any validation function runs) — but this doesn't change the conclusion, since the recursion occurs inside `DecodeRLP`, prior to any `CheckInstallable`/`CheckUpdatable` gate.

### Title
Unbounded Recursive RLP Decoding of Nested `AccountKeyRoleBased` Causes Stack-Overflow DoS - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes embedded `AccountKeySerializer` blobs without any recursion-depth limit. A transaction submitter or public-RPC caller can supply an `AccountKey` blob that nests `AccountKeyRoleBased` inside itself many times, forcing the Go call stack to recurse until it overflows, crashing the node process.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` reads a list of byte-slices and, for each one, calls `rlp.DecodeBytes` into an `AccountKeySerializer`, whose `DecodeRLP` reconstructs an `AccountKey` via `NewAccountKey(keyType)` and recursively decodes into it via `s.Decode(serializer.key)` [1](#0-0) [2](#0-1) . If `keyType == AccountKeyTypeRoleBased`, this chain calls back into `AccountKeyRoleBased.DecodeRLP` again. No function in this chain, nor the underlying `rlp.Stream`/`decodeListSlice` machinery, tracks or bounds recursion depth [7](#0-6) . The only mitigating check, `IsCompositeType`, is enforced solely in `CheckInstallable`/`CheckUpdatable` on a single nesting level, and runs only *after* decoding has already completed (or crashed) [4](#0-3) .

### Impact Explanation
A successful exploit crashes the node process handling the malicious input (goroutine stack overflow is typically fatal in Go, aborting the whole process). If exploited via a transaction that is broadcast to and processed by all full nodes (tx-pool validation and block execution both invoke this decode path), this can cause a synchronized denial-of-service across the network. If exploited via the public `kaia_decodeAccountKey` RPC, it crashes the specific node serving that RPC call.

### Likelihood Explanation
Likelihood is high for the RPC vector (`kaia_decodeAccountKey` accepts arbitrary attacker-controlled `hexutil.Bytes` with no pre-validation) and moderate for the transaction vector (requires crafting a valid `TxTypeAccountUpdate` transaction whose `AccountKey` payload contains deeply nested `AccountKeyRoleBased` encodings, which is straightforward RLP construction requiring no special privileges).

### Recommendation
Add an explicit recursion-depth counter (e.g., passed through `rlp.Stream` or a package-level counter parameter) in `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, rejecting decoding once depth exceeds a small constant (e.g., 1, since nested `RoleBased` is never valid). Enforce this check *during* decode, not only in the post-decode `CheckInstallable`/`CheckUpdatable` validation, so the stack cannot be driven to overflow before validation runs. Apply the same depth limit to the public `DecodeAccountKey` RPC entrypoint.

### Proof of Concept
Conceptual construction (exact byte-level PoC would need to be built and verified with the actual RLP encoder, which requires execution access not available in this analysis):
1. Build an `AccountKeyRoleBased` value `K0` containing a single `AccountKeyLegacy` element and RLP-encode it via `AccountKeySerializer`.
2. Build `K1 = AccountKeyRoleBased{K0}`, encode it the same way.
3. Repeat step 2 N times (e.g., N = 100,000), each time wrapping the previous encoded blob as the single element of a new `AccountKeyRoleBased`.
4. Submit the final encoded blob either as the `AccountKey` field of a `TxTypeAccountUpdate` transaction, or directly to the `kaia_decodeAccountKey` RPC endpoint (`api.KaiaAPI.DecodeAccountKey`).
5. Because each level requires only a few bytes of RLP overhead, N can be made large enough to overflow the goroutine stack while keeping the total payload size well under typical size limits, causing `DecodeRLP` to recurse until the process crashes.

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

**File:** blockchain/types/accountkey/account_key_role_based.go (L233-269)
```go
func (a *AccountKeyRoleBased) CheckUpdatable(newKey AccountKey, currentBlockNumber uint64) error {
	if newKey, ok := newKey.(*AccountKeyRoleBased); ok {
		lenOldKey := len(*a)
		lenNewKey := len(*newKey)
		// If no key is to be replaced, it is regarded as a fail.
		if lenNewKey == 0 {
			return kerrors.ErrZeroLength
		}
		// Do not allow undefined roles.
		if lenNewKey > (int)(RoleLast) {
			return kerrors.ErrLengthTooLong
		}
		for i := range lenNewKey {
			switch {
			// A composite key is not allowed.
			case (*newKey)[i].IsCompositeType():
				return kerrors.ErrNestedCompositeType
			// If newKey is longer than oldKey, init the new attributes.
			case i >= lenOldKey:
				if err := (*newKey)[i].CheckInstallable(currentBlockNumber); err != nil {
					return err
				}
			// Do nothing for AccountKeyTypeNil
			case (*newKey)[i].Type() == AccountKeyTypeNil:

			// Check whether the newKey is replacable or not
			default:
				if err := CheckReplacable((*a)[i], (*newKey)[i], currentBlockNumber); err != nil {
					return err
				}
			}
		}
		return nil
	}
	// Update is not possible if the type is different.
	return kerrors.ErrDifferentAccountKeyType
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

**File:** rlp/decode.go (L303-342)
```go
func decodeListSlice(s *Stream, val reflect.Value, elemdec decoder) error {
	size, err := s.List()
	if err != nil {
		return wrapStreamError(err, val.Type())
	}
	if size == 0 {
		val.Set(reflect.MakeSlice(val.Type(), 0, 0))
		return s.ListEnd()
	}
	if err := decodeSliceElems(s, val, elemdec); err != nil {
		return err
	}
	return s.ListEnd()
}

func decodeSliceElems(s *Stream, val reflect.Value, elemdec decoder) error {
	i := 0
	for ; ; i++ {
		// grow slice if necessary
		if i >= val.Cap() {
			newcap := max(val.Cap()+val.Cap()/2, 4)
			newv := reflect.MakeSlice(val.Type(), val.Len(), newcap)
			reflect.Copy(newv, val)
			val.Set(newv)
		}
		if i >= val.Len() {
			val.SetLen(i + 1)
		}
		// decode into element
		if err := elemdec(s, val.Index(i)); err == EOL {
			break
		} else if err != nil {
			return addErrorContext(err, fmt.Sprint("[", i, "]"))
		}
	}
	if i < val.Len() {
		val.SetLen(i)
	}
	return nil
}
```
