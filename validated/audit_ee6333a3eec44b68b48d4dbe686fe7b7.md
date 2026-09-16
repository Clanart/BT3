### Title
Unbounded recursive RLP decoding of nested `AccountKeyRoleBased` causes stack-consumption DoS via `eth_sendRawTransaction` / `AccountUpdate` transactions - ([File: blockchain/types/accountkey/account_key_role_based.go])

### Summary
`AccountKeyRoleBased.DecodeRLP` recursively decodes each element of a role-based account key by calling `rlp.DecodeBytes` on an `AccountKeySerializer`, which in turn calls `s.Decode(serializer.key)` for whatever `AccountKeyType` byte is present in the payload — including `AccountKeyTypeRoleBased` again. There is no depth limit enforced during decoding, so a single submitted `AccountUpdate`/`FeeDelegatedAccountUpdate` transaction (or a raw account-key blob decoded via the `kaia_decodeAccountKey` RPC) can encode thousands of nested `RoleBased` keys, each adding a Go call-stack frame during `DecodeRLP`. This is the same bug class as CVE-2018-16369 (Xpdf `XRef::fetch`/`AcroForm::scanField` unbounded recursive parsing causing stack consumption), just moved from PDF parsing to Kaia's RLP account-key parsing.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` is defined as: [1](#0-0) 

For each byte-string element `b` in the decoded `[][]byte`, it calls `rlp.DecodeBytes(b, &serializer)`, which invokes `AccountKeySerializer.DecodeRLP`: [2](#0-1) 

`NewAccountKey(serializer.keyType)` allocates a key object based on the attacker-controlled `keyType` byte, and if that type is again `AccountKeyTypeRoleBased`, `s.Decode(serializer.key)` re-enters `AccountKeyRoleBased.DecodeRLP`, recursing. Nothing in this call chain tracks or bounds recursion depth.

The only place nested/composite role-based keys are rejected is `CheckInstallable`/`CheckUpdatable`, which run **after** the entire structure has already been fully RLP-decoded: [3](#0-2) 

This means the "no nested RoleBasedKey" rule is a semantic validation check performed post-decode; it does not prevent the recursive decode itself from executing to unbounded depth first.

The relevant transaction path is: raw bytes → `Transaction.DecodeRLP`/`UnmarshalBinary` → `newTxInternalDataSerializer()` decode (dispatches to `TxInternalDataAccountUpdate` and similar types, whose `Key` field is an `accountkey.AccountKeySerializer`) → `AccountKeyRoleBased.DecodeRLP` recursion: [4](#0-3) 

Because `MaxTxDataSize` (128 KB) is only checked in `TxPool.validateTx` **after** the transaction object has already been RLP-decoded: [5](#0-4) 

the recursive decoding (and thus the potential stack growth) happens unconditionally on any raw transaction bytes handed to the node before any size or structural sanity check can reject it. A 128 KB payload gives ample room (each nesting level costs only a few bytes — a type byte plus small list/string headers) to encode on the order of tens of thousands of nested RoleBased levels.

This mirrors CVE-2018-16369's structure: an unbounded recursive parser (`AcroForm::scanField`/`XRef::fetch` in Xpdf) is fed attacker-controlled nested structures from an untrusted file, exhausting the stack. Here, the untrusted input is a transaction's account-key payload, and the recursive parser is `AccountKeyRoleBased.DecodeRLP`/`AccountKeySerializer.DecodeRLP`.

### Impact Explanation
Excessive recursion in Go, once it exceeds the runtime's maximum goroutine stack size, results in a **fatal, unrecoverable runtime error** ("stack overflow"), which terminates the entire process — not just the offending goroutine (unlike a `panic`, this cannot be caught with `recover()`). Any full/public RPC node or peer that decodes such a crafted `AccountUpdate` transaction (submitted via `eth_sendRawTransaction`/`kaia_sendRawTransaction`, gossiped over p2p, or included in a block) is at risk of a process crash — a denial-of-service reachable by any unprivileged transaction sender or public RPC caller. This matches the CVSS 5.5 rating and DoS characterization of the original CVE.

### Likelihood Explanation
- Reachable with a single, self-signed `AccountUpdate` (or fee-delegated variant) transaction — no special privileges, governance access, or validator/peer position needed.
- The transaction size cap (`MaxTxDataSize` = 128 KB) still allows deep nesting because per-level overhead is minimal (a handful of RLP header/type bytes per level).
- The vulnerable recursive decode executes before any of the higher-level sanity checks (`ErrLengthTooLong`, `ErrNestedCompositeType`) can short-circuit it, since those checks operate on the already-fully-decoded structure.
- Exploitability depends on exact Go runtime stack growth limits and how deeply nested structures can be packed per byte, which requires empirical verification (stack limit is configurable and typically large, e.g., 1 GB default max for goroutines on 64-bit); actual crash may require tuning nesting density, but the code path unquestionably lacks any explicit recursion-depth guard.

### Recommendation
- Enforce a maximum recursion/nesting depth check in `AccountKeyRoleBased.DecodeRLP` (and in `AccountKeySerializer.DecodeRLP`) *before* recursing further — e.g., pass down and check a depth counter, rejecting any key whose `AccountKeyType` is `AccountKeyTypeRoleBased` when already inside a RoleBased decode (mirroring the existing `CheckInstallable`/`isNested` semantic rule, but enforced at decode time, not only at validation time).
- Alternatively/additionally, bound the total number of RLP `Decoder` recursion levels globally in the `rlp` package (similar to a stream depth limit) so that any custom `Decoder` implementation cannot be abused for stack-exhaustion regardless of the specific type.
- Move `MaxTxDataSize`/basic structural sanity checks to occur prior to, or interleaved with, RLP decoding of complex nested fields where feasible, or process untrusted RLP decoding using a size/nesting-limited stream.

### Proof of Concept
Conceptually, craft an `AccountKeySerializer` payload where `keyType = AccountKeyTypeRoleBased` and its single "role key" element is itself another `AccountKeySerializer` with `keyType = AccountKeyTypeRoleBased`, repeated N times (e.g., N in the tens of thousands, packed to fit within `MaxTxDataSize` = 128 KB, given ~4–6 bytes overhead per nesting level):

```
enc_0 = AccountKeySerializer(AccountKeyTypeNil, NilKey)              // innermost terminator
enc_i = AccountKeySerializer(AccountKeyTypeRoleBased, [enc_{i-1}])   // for i = 1..N
```

Embed `enc_N` as the `Key` field of an `AccountUpdate` transaction (`TxInternalDataAccountUpdate`), sign it, and submit the raw RLP bytes via `kaia_sendRawTransaction` or `eth_sendRawTransaction`, or via `kaia_decodeAccountKey`/`DecodeAccountKey` RPC directly on the encoded key bytes: [6](#0-5) 

Submitting/decoding this payload drives `AccountKeyRoleBased.DecodeRLP` → `AccountKeySerializer.DecodeRLP` recursion N levels deep before any `CheckInstallable`/size-limit rejection occurs, exercising unbounded Go call-stack growth on the node handling the request.

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

**File:** blockchain/types/transaction.go (L239-266)
```go
// DecodeRLP implements rlp.Decoder
func (tx *Transaction) DecodeRLP(s *rlp.Stream) error {
	serializer := newTxInternalDataSerializer()
	if err := s.Decode(serializer); err != nil {
		return err
	}

	if !SanityCheckSignatures(serializer.tx.RawSignatureValues(), serializer.tx.Type()) {
		return ErrInvalidSig
	}

	size := calculateTxSize(serializer.tx)
	tx.setDecoded(serializer.tx, int(size))

	return nil
}

// UnmarshalBinary decodes the canonical encoding of transactions.
// It supports legacy RLP transactions and EIP2718 typed transactions.
func (tx *Transaction) UnmarshalBinary(b []byte) error {
	newTx := &Transaction{}
	if err := rlp.DecodeBytes(b, newTx); err != nil {
		return err
	}

	tx.setDecoded(newTx.data, len(b))
	return nil
}
```

**File:** blockchain/tx_pool.go (L884-889)
```go
	// Reject transactions over MaxTxDataSize to prevent DOS attacks
	// Note: Sidecar are not included in the size calculation.
	// Sidecar-specific validation must be done elsewhere.
	if uint64(tx.SizeWithoutBlobTxSidecar()) > MaxTxDataSize {
		return ErrOversizedData
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
