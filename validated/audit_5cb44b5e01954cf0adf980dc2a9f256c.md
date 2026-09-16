### Title
Excessive Recursion in AccountKeyRoleBased RLP Decoding Leads to Stack-Overflow DoS - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each sub-key by re-entering the generic `AccountKeySerializer` decode path, and this recursion is completely unbounded — the "no nested composite type" rule is only enforced *after* decoding finishes, inside `CheckInstallable`/`CheckUpdatable`. This mirrors the CVE-2018-11254 pattern (`PdfPagesTree::GetPageNode()` recursing on attacker-controlled nested structures before any depth validation), applied here to Kaia's `TxTypeAccountUpdate` account-key deserialization.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte strings and, for every element, calls `rlp.DecodeBytes(b, &serializer)`, which in turn calls `AccountKeySerializer.DecodeRLP`: [1](#0-0) 
`AccountKeySerializer.DecodeRLP` reads the key type and then calls `s.Decode(serializer.key)`, which — if the type is again `AccountKeyTypeRoleBased` — re-enters `AccountKeyRoleBased.DecodeRLP`: [2](#0-1) 

There is no depth counter or recursion limit anywhere along this call chain, and the underlying `rlp` `Stream`/`decode.go` machinery likewise imposes no nesting-depth limit (only byte-size/list-size limits), so the decoder will happily recurse for every level of nesting present in the input bytes: [3](#0-2) 

The only protection against nested `AccountKeyRoleBased` structures — `IsCompositeType()` checks in `CheckInstallable`/`CheckUpdatable` — is applied *after* the entire structure has already been fully decoded: [4](#0-3) [5](#0-4) 

Because RLP encoding is extremely compact for nesting (each additional level costs only a few bytes of list/type-byte overhead), an attacker can craft a deeply nested `AccountKeyRoleBased` payload (RoleBased → RoleBased → RoleBased → ...) that stays well within normal transaction size limits while forcing thousands of recursive Go function calls during decode, before validation ever gets a chance to reject it.

### Impact Explanation
Any unprivileged party can submit a `TxTypeAccountUpdate` (or fee-delegated variant) transaction whose `AccountKey` payload encodes a deeply nested `AccountKeyRoleBased` value. Every node that decodes this transaction — via `eth_sendRawTransaction`, transaction pool propagation, or block processing — will recurse through `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` proportional to the attacker-chosen nesting depth. Sufficiently deep nesting can exhaust the goroutine stack, crashing the node process (panic/stack overflow) that is decoding the transaction — a denial-of-service reachable from a single submitted transaction, matching the CVE-2018-11254 bug class (uncontrolled recursion on attacker-supplied structured input prior to validation).

### Likelihood Explanation
High: no privileges, staking, or special roles are required — only the ability to submit or broadcast a single RLP-encoded transaction (or raw account-key bytes via `DecodeAccountKey` RPC, which also decodes user-supplied bytes through the same `AccountKeySerializer`/`AccountKeyRoleBased` path): [6](#0-5) 
Crafting deeply nested RLP is trivial and inexpensive in payload size.

### Recommendation
Enforce a maximum recursion/nesting depth check at the very start of `AccountKeyRoleBased.DecodeRLP` (and/or `AccountKeySerializer.DecodeRLP`), rejecting the input before recursing further once a small fixed depth (e.g., 1, since nested `RoleBased` is never valid) is exceeded — mirroring the `IsCompositeType` rule but applied during decode rather than only after full decode completes. Alternatively, add a global depth counter to the `rlp.Stream`/decoder that aborts decoding once a configurable maximum nesting depth is reached, independent of the specific type being decoded.

### Proof of Concept
1. Programmatically construct nested `AccountKeyRoleBased` values: `roleN = AccountKeyRoleBased{roleN-1}`, repeated for N levels (e.g., N = 10,000), each wrapping the previous one exactly as done in tests such as `TestAccountUpdateRoleBasedKeyNested`: [7](#0-6) 
2. RLP-encode the outermost `AccountKeyRoleBased` (this is compact — a few bytes overhead per nesting level).
3. Submit a `TxTypeAccountUpdate` transaction (or call `DecodeAccountKey`) with this account key payload to a node via `eth_sendRawTransaction` / RPC.
4. Observe that `AccountKeyRoleBased.DecodeRLP` recurses N times through `AccountKeySerializer.DecodeRLP` before the `CheckInstallable`/`CheckUpdatable` nested-composite-type rejection is ever reached, causing excessive stack growth and, for sufficiently large N, a stack overflow/panic on the decoding node.

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

**File:** rlp/decode.go (L1161-1179)
```go
// willRead is called before any read from the underlying stream. It checks
// n against size limits, and updates the limits if n doesn't overflow them.
func (s *Stream) willRead(n uint64) error {
	s.kind = -1 // rearm Kind

	if inList, limit := s.listLimit(); inList {
		if n > limit {
			return ErrElemTooLarge
		}
		s.stack[len(s.stack)-1] = limit - n
	}
	if s.limited {
		if n > s.remaining {
			return ErrValueTooLarge
		}
		s.remaining -= n
	}
	return nil
}
```

**File:** api/api_kaia.go (L173-200)
```go
}

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
	}
	return nil
}
```

**File:** tests/account_keytype_test.go (L1737-1739)
```go
	nestedAccKey := accountkey.NewAccountKeyRoleBasedWithValues(accountkey.AccountKeyRoleBased{
		roleKey.AccKey,
	})
```
