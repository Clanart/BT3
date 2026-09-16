### Title
Unbounded recursive decoding of nested `AccountKeyRoleBased` allows stack-exhaustion DoS via a crafted `AccountUpdate`/`FeeDelegatedAccountUpdate` transaction - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
The Exiv2 report describes an unbounded-recursion parser (`printIFDStructure`) that walks attacker-controlled nested structures with no depth limit, exhausting the stack and crashing the process. Kaia's `AccountKeyRoleBased` RLP decoder exhibits the same bug class: it recursively decodes account-key sub-elements with no depth check, and this decoder is reached directly from an untrusted, attacker-submitted transaction.

### Finding Description
`AccountKeyRoleBased.DecodeRLP` decodes an outer list of byte strings and, for every element, calls `rlp.DecodeBytes` into a fresh `AccountKeySerializer`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a `keyType`, instantiates the corresponding `AccountKey` via `NewAccountKey`, and then recursively invokes that key's own `DecodeRLP`: [2](#0-1) 

If the encoded `keyType` is again `AccountKeyTypeRoleBased`, this chain (`AccountKeyRoleBased.DecodeRLP` → `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` → …) repeats with no depth counter or limit anywhere in the call path. Each level of nesting consumes a new Go call stack frame (plus a new `rlp.Stream` from the pool, reflection-based struct decoding, etc.), so an attacker only needs to encode a chain of nested `RoleBased` wrappers, each just a few bytes (type byte + short list header), to achieve very deep recursion with a small, gas/size-bounded transaction payload.

Critically, the *only* place that rejects nested composite account keys is `AccountKeyRoleBased.CheckInstallable`/`CheckUpdatable`, which run during transaction application/state-transition — i.e. **after** `DecodeRLP` has already fully executed and, in the malicious case, already exhausted the stack: [3](#0-2) 

Because `Transaction.DecodeRLP` / `UnmarshalBinary` is invoked on every incoming transaction (mempool admission via `AddRemote`, `eth_sendRawTransaction`, and block/tx propagation) before any semantic validation such as `CheckInstallable` runs, the decoder is reachable by any unprivileged party submitting a transaction: [4](#0-3) 

The RLP package itself does not impose a decode-depth limit; nesting-depth protection is left entirely to the “no nested composite type” rule that is checked far too late (post-decode), analogous to the Exiv2 `printIFDStructure` recursion where IFD tags could reference themselves/each other with no depth cap.

### Impact Explanation
A crafted `TxTypeAccountUpdate` (or fee-delegated variant) with a deeply nested `AccountKeyRoleBased` value, when decoded by any node (tx-pool admission, RPC decode, or block processing), can drive the decoding call stack to exhaustion, causing a goroutine/process crash. Since transaction decoding happens uniformly across all full nodes/validators that receive or process the transaction (via RPC submission or p2p propagation prior to inclusion), a single malicious transaction can potentially crash multiple nodes that attempt to decode it, which is a network-wide availability impact reachable from a normal, unprivileged transaction sender — matching the “Medium” severity class of the analog CVE (stack-exhaustion DoS via crafted input, CVSS 6.5, AV:N/AC:L/PR:N).

### Likelihood Explanation
Likelihood is limited by whether the achievable recursion depth within transaction-size/gas constraints is sufficient to exhaust a typical goroutine stack. Go grows goroutine stacks dynamically (default max ~1GB), and each recursive decode level here involves multiple non-trivial stack frames (reflection-driven `rlp.Decode`, `AccountKeySerializer.DecodeRLP`, `AccountKeyRoleBased.DecodeRLP`, plus intermediate helper calls), so the per-level stack cost is larger than a single byte per level, making the attack payload-size-to-depth ratio favorable compared to naive `[[[[...]]]]` recursion bugs. I was not able to conclusively verify the exact tx size limit constant or the exact per-level stack-frame cost in this pass, so the practical achievability of a full-stack crash (versus merely a slow/expensive decode) is uncertain and would need to be confirmed by a proof-of-concept encoding and stack-depth measurement.

### Recommendation
- Add an explicit recursion/nesting-depth counter (e.g., limit to 1 level, since only one level of `RoleBased` nesting is ever semantically valid) inside `AccountKeyRoleBased.DecodeRLP` / `AccountKeySerializer.DecodeRLP`, rejecting a nested `AccountKeyTypeRoleBased` immediately at decode time instead of only after decoding completes in `CheckInstallable`.
- Alternatively, enforce a global maximum RLP decode/recursion depth in the shared `rlp` package that all custom `DecodeRLP` implementations inherit, so no single object graph (account keys or otherwise) can recurse unbounded.
- Add a fuzz/unit test that submits an `AccountUpdate` transaction with hundreds/thousands of nested `RoleBased` account keys and asserts the node returns a decode error instead of crashing.

### Proof of Concept
Conceptual encoding (not run against the live code in this pass):
1. Build an `AccountKeyPublic` (or any non-composite key) as the innermost key.
2. Wrap it in an `AccountKeySerializer` encoding (`keyType || key`).
3. Wrap that serialized bytes as the single element of an `AccountKeyRoleBased` list, re-serialize as `AccountKeySerializer` with `keyType = AccountKeyTypeRoleBased`.
4. Repeat step 3 N times (e.g., N = 50,000), nesting each serialized blob inside the next.
5. Place the final nested blob as the `AccountKey` field of a `TxTypeAccountUpdate` transaction, RLP-encode the transaction, and submit it via `eth_sendRawTransaction` / `txpool.AddRemote`.
6. Observe whether `Transaction.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recursion exhausts the goroutine stack before `CheckInstallable`'s nested-type rejection is ever reached.

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
