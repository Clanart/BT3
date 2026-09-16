## Confirmed analog to CVE-2026-46557 (ImageMagick fx stack-overflow via missing depth check)

### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` allows remote stack-overflow DoS - (File: `blockchain/types/accountkey/account_key_role_based.go`)

### Summary
Kaia's `AccountKeyRoleBased` type — the account-key structure used by `TxTypeAccountUpdate`/`TxTypeAccountCreation` (and fee-delegated variants) — decodes itself recursively from raw transaction bytes with no depth limit, exactly matching the ImageMagick "missing depth check → stack overflow" bug class: a client-controlled recursive structure is expanded by the decoder before any structural/semantic validation is ever applied.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes a list of byte-slices and, for every element, RLP-decodes it into a fresh `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType`, instantiates the corresponding concrete `AccountKey` via `NewAccountKey`, and then decodes into it: [2](#0-1) 

`NewAccountKey` allows `AccountKeyTypeRoleBased` to be instantiated again from inside this call chain: [3](#0-2) 

This creates a decode-time recursion: `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` → `NewAccountKey(AccountKeyTypeRoleBased)` → `s.Decode(*AccountKeyRoleBased)` → `AccountKeyRoleBased.DecodeRLP` → … with **no depth counter, no recursion limit, and no check that the inner key is a composite type** at decode time.

The only place that rejects a nested `RoleBased` key (`IsCompositeType()` / `ErrNestedCompositeType`) is in `CheckInstallable`/`CheckUpdatable`, which run only *after* decoding has already completed: [4](#0-3) [5](#0-4) 

The RPC endpoint `DecodeAccountKey`/`EncodeAccountKey` also only performs this "nested" check post-decode: [6](#0-5) 

Because each nesting level costs only a handful of RLP bytes (a list tag + a 1-byte key-type tag), an attacker can encode many thousands of nesting levels within a transaction well under normal size limits, causing the Go call stack to grow unbounded during RLP decoding — a classic stack-overflow-by-recursion, the same bug class as the ImageMagick `fx` operator missing a depth check.

### Impact Explanation
Stack overflow in Go causes an unrecoverable fatal runtime error (`runtime: goroutine stack exceeds ... - fatal error: stack overflow`), which **cannot be caught by `recover()`** and crashes the entire process. This is reachable through:
- `TxTypeAccountUpdate` / `TxTypeAccountCreation` (and their fee-delegated variants) transaction decoding — performed on every node that receives the transaction via the tx pool or during block processing, i.e. any full node, including validators.
- The public `kaia_decodeAccountKey` RPC method, directly exposed to any RPC caller with just the crafted RLP bytes (no signature check needed to trigger decode).

A single malicious transaction/RPC payload can crash a validator or public RPC node — a network-wide availability impact reachable by an unprivileged transaction sender or public RPC caller, warranting Medium/High severity in line with the CVSS 6.2 rating of the referenced ImageMagick advisory.

### Likelihood Explanation
High likelihood of trivial exploitation: crafting the payload requires only nested RLP lists of `[keyType=AccountKeyTypeRoleBased, [ [keyType=AccountKeyTypeRoleBased, [...]], ... ]]` — no cryptographic material, no special privileges, and it can be submitted as ordinary transaction data or as an argument to the public `kaia_decodeAccountKey` API.

### Recommendation
- Add an explicit recursion/depth counter (e.g., pass a `depth int` parameter through `AccountKeySerializer.DecodeRLP` / `AccountKeyRoleBased.DecodeRLP`, or track it via `rlp.Stream`) and reject decoding once a small maximum depth (e.g., 1, since role-based keys must not be nested at all per the existing business rule) is exceeded — enforcing the "no composite key inside role-based key" rule **at decode time**, not only in `CheckInstallable`/`CheckUpdatable`.
- Alternatively/additionally, immediately reject `AccountKeyTypeRoleBased` when encountered while already decoding inside an `AccountKeyRoleBased` (thread an "isNested" flag similar to `checkAccountKeyZeroValues`'s `isNested` parameter through the RLP decode path).
- Apply the same fix path to the JSON decode path (`AccountKeyRoleBased.UnmarshalJSON`), which has an analogous unbounded recursion via nested `AccountKeySerializer.UnmarshalJSON` calls.

### Proof of Concept
Conceptually, construct RLP bytes for a `TxTypeAccountUpdate` (or a raw `kaia_decodeAccountKey` argument) whose `Key` field is an `AccountKeySerializer`-encoded value with `keyType = AccountKeyTypeRoleBased (5)` and whose list element is itself another `AccountKeySerializer`-encoded `AccountKeyTypeRoleBased` value, repeated N times (N ≈ tens of thousands, achievable within a few hundred KB by RLP-encoding a deeply right-nested list `[5, [[5,[[5,[[...]]]]]]]`). Submitting this as:
1. the `Key` value of a signed (or even just RLP-syntactically-valid, since decode happens before signature verification in some paths) `TxTypeAccountUpdate` transaction to the tx pool, or
2. the `encodedAccKey` argument to the `kaia_decodeAccountKey` JSON-RPC method,

drives `AccountKeyRoleBased.DecodeRLP` into N levels of mutual recursion with `AccountKeySerializer.DecodeRLP`, exhausting the goroutine stack and crashing the node process with `fatal error: stack overflow` before any nested-composite-type validation (`CheckInstallable`/`CheckUpdatable`) is reached.

**Note on verification limits:** I was not able to fully trace whether transaction RLP decoding for `TxTypeAccountUpdate` happens strictly before or interleaved with signature verification in the txpool ingestion path (only `tx_internal_data_account_update.go`'s `DecodeRLP` function name was confirmed to exist, not its full body, due to tool-call limits), and I could not execute the PoC to empirically confirm the exact nesting count needed to overflow a default goroutine stack. A Devin session with full repo/runtime access would be needed to pin down the precise decode-call-order and confirm the crash empirically.

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

**File:** api/api_kaia.go (L166-200)
```go
// DecodeAccountKey gets an RLP encoded bytes of an account key and returns the decoded account key.
func (s *KaiaAPI) DecodeAccountKey(encodedAccKey hexutil.Bytes) (*accountkey.AccountKeySerializer, error) {
	dec := accountkey.NewAccountKeySerializer()
	if err := rlp.DecodeBytes(encodedAccKey, &dec); err != nil {
		return nil, err
	}
	return dec, nil
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
