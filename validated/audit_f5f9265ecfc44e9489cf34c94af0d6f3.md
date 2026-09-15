### Title
Unbounded recursive `AccountKey` RLP decoding allows crafted nested `AccountKeyRoleBased` payloads to cause stack-exhaustion DoS - (File: blockchain/types/accountkey/account_key_role_based.go)

### Summary
`AccountKeyRoleBased.DecodeRLP` and `AccountKeySerializer.DecodeRLP` recursively decode account keys via nested `rlp.DecodeBytes` calls with no depth limit. Because a `RoleBased` key type dispatches back into `AccountKeyRoleBased.DecodeRLP` for each inner element, an attacker can construct an RLP-encoded key that is nested to an arbitrary depth, causing unbounded Go call-stack recursion during decoding — the same bug class as ALPINE-CVE-2017-9208 (unbounded recursive resolution of nested structures causing stack exhaustion).

### Finding Description
`AccountKeyRoleBased.DecodeRLP` reads a list of opaque byte strings and, for each one, calls `rlp.DecodeBytes(b, &serializer)`: [1](#0-0) 

`AccountKeySerializer.DecodeRLP` reads a key-type tag, instantiates the corresponding `AccountKey` via `NewAccountKey`, and then calls `s.Decode(serializer.key)`: [2](#0-1) 

If the decoded `keyType` is `AccountKeyTypeRoleBased`, `s.Decode(serializer.key)` re-enters `AccountKeyRoleBased.DecodeRLP`, which again slices its input into byte strings and recurses into `AccountKeySerializer.DecodeRLP` for each of them. Because each nesting level only requires a small constant number of RLP bytes (an outer list wrapper + a byte-string element + a 1-byte type tag), an attacker can encode many thousands of nesting levels within a payload of a few hundred KB, driving unbounded Go function-call recursion with no depth check anywhere in the decode path.

The application-level rejection of nested `RoleBased` keys (`ErrNestedCompositeType`) only happens *after* decoding completes, at validation time in `CheckInstallable`/`CheckUpdatable`: [3](#0-2) 

This is too late — the stack-exhausting recursion already occurs during `DecodeRLP`, before any nesting check can run, exactly mirroring the qpdf bug class where unbounded recursive resolution of a crafted structure exhausts the stack before any sanity check is reached.

### Impact Explanation
A Go stack overflow triggered by unbounded recursion is not a normal recoverable panic — it terminates the process (`fatal error: stack overflow`), crashing the node. This is reachable through two independent public entry points:

1. **Public RPC** — `KaiaAPI.DecodeAccountKey` accepts arbitrary attacker-controlled bytes and passes them straight to `rlp.DecodeBytes(encodedAccKey, &dec)` with no prior validation or depth limiting: [4](#0-3) 

2. **Unprivileged transaction sender** — `TxTypeAccountUpdate` (and fee-delegated variants) transactions carry an `AccountKey` field that is decoded through the same `AccountKeySerializer`/`AccountKeyRoleBased` recursive path when a raw transaction is decoded (e.g., via `eth_sendRawTransaction`, or when a node parses transactions received over the network/txpool before block inclusion).

Either path lets any unauthenticated caller crash a full/validator/RPC node process, which is a concrete availability impact against consensus/RPC infrastructure — qualifying as Medium severity, consistent with the CVSS 5.5 rating of the source advisory (local DoS via crafted parsed input).

### Likelihood Explanation
Likelihood is high: the RPC method `kaia_decodeAccountKey` requires no authentication, no fees, and no prior account state — a single call with a crafted byte string is sufficient. The transaction path requires only a validly-shaped (but not necessarily "valid" in the composite-type sense) `AccountUpdate` transaction to be submitted or gossiped; signature/type validation for AccountKey composite-nesting occurs strictly after decoding, so the crash occurs regardless of whether the transaction would ultimately be rejected.

### Recommendation
- Add an explicit recursion/nesting depth limit inside `AccountKeyRoleBased.DecodeRLP` (and more generally in `AccountKeySerializer.DecodeRLP`), rejecting any encoding that would cause a `RoleBased` key to decode another `RoleBased` key before performing the recursive `rlp.DecodeBytes` call — i.e., perform the "no nested composite type" check during decode, not only during `CheckInstallable`/`CheckUpdatable`.
- Alternatively/also enforce a global maximum recursion depth counter threaded through `AccountKeySerializer.DecodeRLP`/`AccountKeyRoleBased.DecodeRLP`, returning an error once exceeded, mirroring the fix pattern used for similar "infinite recursion" PDF/ASN.1 parser CVEs (bound recursion before descending).
- Apply the same bound to `KaiaAPI.DecodeAccountKey` and any other public entry point that calls `rlp.DecodeBytes` on `AccountKeySerializer`.

### Proof of Concept
Conceptually (would need to be executed in a Devin session with codebase access to build exact byte sequences):
1. Encode a minimal `AccountKeyRoleBased` value `R0` (an RLP list containing zero or one placeholder byte-string entries).
2. Encode `R1` as an `AccountKeySerializer`-wrapped key of type `AccountKeyTypeRoleBased` whose payload is itself the RLP encoding of a `RoleBased` list containing `R0`'s bytes as one element (per `AccountKeyRoleBased.EncodeRLP`, each element is `rlp.EncodeToBytes(NewAccountKeySerializerWithAccountKey(...))`).
3. Repeat step 2 tens of thousands of times, each time wrapping the previous result, to build `Rn`.
4. Submit `Rn`'s bytes to the `kaia_decodeAccountKey` RPC method, or embed `Rn` as the `Key` field of a `TxTypeAccountUpdate` transaction and submit it via `eth_sendRawTransaction`.
5. Observe the node process crash with `fatal error: stack overflow` during `rlp.DecodeBytes` → `AccountKeySerializer.DecodeRLP` → `AccountKeyRoleBased.DecodeRLP` recursion, before any composite-type validation runs.

Note: I could not fully trace the exact RLP decode path used by `tx_internal_data_account_update.go` for the `Key` field within available search results (only 9 pattern matches were found, not read line-by-line) — the RPC path (`KaiaAPI.DecodeAccountKey`) is the most directly confirmed and minimal-friction reachable entry point. A background Devin session with full file access should verify the exact transaction-decode call chain to precisely confirm the tx-submission path as well.

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
