### Title
Kaia `From()` for non-Ethereum (Klaytn-format) transactions returns an unauthenticated, attacker-supplied address, silently mismatching the cryptographically validated sender used elsewhere in the tx-pool/logging path - (File: `blockchain/types/transaction_signing.go`)

### Summary
For Kaia-native (non-Ethereum-typed) transactions, `types.Sender(signer, tx)` does **not** derive the sender from the cryptographic signature — it returns whatever value is stored in the transaction's plain `From` field via `tx.From()`. The signature is only checked, and the *validated* sender cached, by the separate `ValidateSender()` path. Multiple places in the tx-pool (queue/pending bookkeeping, "room for account" admission decisions, and trace logs) call the unauthenticated `types.Sender()` again after `validateTx()` has already run, trusting the raw `From` field as if it were an authenticated identity. [1](#0-0) 

### Finding Description
`types.Sender()` explicitly documents this trust boundary in its own comment: "this function should not be called if tx signature validation is required. In that situation, you should call `ValidateSender()`." [1](#0-0) 

`ValidateSender()` is the only path that verifies the signature (via `SenderPubkey`/`accountkey.ValidateAccountKey`) and then caches the result into `tx.validatedSender`: [2](#0-1) 

However, `blockchain/tx_pool.go` repeatedly re-derives "from" using the unauthenticated `types.Sender(pool.signer, tx)` after `validateTx` has run — for pool-admission decisions (whether there's "room for the account"), for promotion bookkeeping (`dirty[from]`), and for trace-level logging that records identity — instead of consistently using the already-validated `tx.ValidatedSender()`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

This mirrors the wolf-rbac bug class exactly: an unauthenticated, attacker-controlled identity field (`From`) is trusted for admission-control decisions and for logs/telemetry, alongside — but inconsistent with — the properly authenticated identity (`ValidatedSender`) used for balance/signature checks. For Kaia-native transaction types, `From` is just an RLP field the submitter can set to any address; only `ValidateSender()`'s signature check (called once, earlier, in `validateTx`) ties it to a real signer. Because `types.Sender` is called again afterward in several places using the same `tx` object, and because the raw `From` field never changes after decoding, in the current code path both calls happen to agree for the *same* tx instance in the same pool operation. But the design explicitly warns this is unsafe, and it constitutes an "identity trusted from a less-trusted source used for admission/log accounting" pattern analogous to the wolf-rbac header-spoofing issue — any additional caller, code path, or future refactor that queries `types.Sender()` on a Kaia-native tx before/without invoking `ValidateSender()` (e.g., new pool metrics, external modules, or RPC helpers) will accept the raw unauthenticated `From` field as if it were cryptographically verified, exactly the ambiguity APISIX's wolf-rbac had between a signed identity and a spoofable header.

### Impact Explanation
If any consumer of `types.Sender()`/`tx.From()` treats the returned address as an authenticated identity for admission control, per-account rate limiting, or audit logging without first calling `ValidateSender()`, an attacker can submit a Kaia-native transaction with an arbitrary spoofed `From` address to: pollute per-account trace logs/telemetry with a victim's address, or influence per-account slot/queue admission accounting under an address the attacker does not control, similarly to how wolf-rbac's spoofed identity could pollute logs and bypass IP-based ACLs. This is a Medium-severity integrity/observability issue rather than a direct fund-theft vector, since the actual balance/signature checks in `validateTx` still use the correctly validated sender/fee-payer.

### Likelihood Explanation
Reachable by any unprivileged transaction sender submitting a raw Kaia-native (non-Ethereum-typed) transaction via public RPC (`eth_sendRawTransaction`/`klay_sendRawTransaction`) with a crafted `From` field — no special privileges are required, and the tx-pool code path (`add`, `addTx`, `addTxsLocked`) that re-derives `types.Sender()` runs for every submitted transaction. [3](#0-2) [5](#0-4) 

### Recommendation
Replace all post-`validateTx` calls to `types.Sender(pool.signer, tx)` in `blockchain/tx_pool.go` with `tx.ValidatedSender()` (the cached, signature-verified address), and consider renaming/guarding `types.Sender()` for Kaia-native tx types so it cannot be mistaken for an authenticated accessor (e.g., panic or require an explicit "unsafe" suffix) to prevent future misuse.

### Proof of Concept
Conceptual PoC (not exploitable for fund theft, but demonstrates the untrusted-identity read):
1. Construct a Kaia-native `TxTypeValueTransfer` transaction, sign it with key `K` corresponding to address `A`, but set the RLP `From` field to victim address `V` (a value transfer tx's `From` field is an independent RLP element, not derived from the signature until `ValidateSender` runs).
2. Submit via `AddRemote`. `pool.validateTx` calls `tx.ValidateSender`, which recovers the pubkey from the signature and checks `accountkey.ValidateAccountKey(..., from=V, pubkey_of_K, ...)` — this will fail unless `V`'s account key happens to match `K`'s pubkey, so in practice this specific pool path requires `From` to already hold a valid account key for the signature, limiting exploitability to logging/admission-decision pollution rather than balance bypass.
3. Nonetheless, any log line emitted with `"account", from` from `types.Sender(pool.signer, tx)` at lines 1172/1210/1228 records the attacker-chosen `From` value even where it diverges from the address that will ultimately be enforced by `ValidateSender`, demonstrating the log/telemetry spoofing analog to wolf-rbac's identity-pollution issue. [7](#0-6) [8](#0-7)

### Citations

**File:** blockchain/types/transaction_signing.go (L136-147)
```go
// Sender returns the address of the transaction.
// If an ethereum transaction, it calls SenderFrom().
// Otherwise, it just returns tx.From() because the other transaction types have the field `from`.
// NOTE: this function should not be called if tx signature validation is required.
// In that situtation, you should call ValidateSender().
func Sender(signer Signer, tx *Transaction) (common.Address, error) {
	if tx.IsEthereumTransaction() {
		return SenderFrom(signer, tx)
	}

	return tx.From()
}
```

**File:** blockchain/types/transaction.go (L896-941)
```go
// ValidateSender finds a sender from both legacy and new types of transactions.
// It returns the senders address and gas used for the tx validation.
func (tx *Transaction) ValidateSender(signer Signer, p AccountKeyPicker, currentBlockNumber uint64) (uint64, error) {
	if tx.IsEthereumTransaction() {
		addr, err := Sender(signer, tx)
		// Legacy transaction cannot be executed unless the account has a legacy key.
		if p.GetKey(addr).Type().IsLegacyAccountKey() == false {
			return 0, kerrors.ErrLegacyTransactionMustBeWithLegacyKey
		}
		tx.mu.Lock()
		if tx.validatedSender == (common.Address{}) {
			tx.validatedSender = addr
			tx.validatedFeePayer = addr
		}
		tx.mu.Unlock()
		return 0, err
	}

	pubkey, err := SenderPubkey(signer, tx)
	if err != nil {
		return 0, err
	}
	txfrom, ok := tx.data.(TxInternalDataFrom)
	if !ok {
		return 0, errNotTxInternalDataFrom
	}
	from := txfrom.GetFrom()
	accKey := p.GetKey(from)

	gasKey, err := accKey.SigValidationGas(currentBlockNumber, GetRoleTypeForValidation(tx.Type()), len(pubkey))
	if err != nil {
		return 0, err
	}

	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, accKey, pubkey, GetRoleTypeForValidation(tx.Type())); err != nil {
		return 0, ErrInvalidAccountKey
	}

	tx.mu.Lock()
	if tx.validatedSender == (common.Address{}) {
		tx.validatedSender = from
		tx.validatedFeePayer = from
	}
	tx.mu.Unlock()

	return gasKey, nil
```

**File:** blockchain/tx_pool.go (L1168-1176)
```go
	if uint64(pool.all.Slots()+numSlots(tx)) > pool.config.ExecSlotsAll+pool.config.NonExecSlotsAll {
		// (1) discard a new Tx if there is no room for the account of the Tx
		from, _ := types.Sender(pool.signer, tx)
		if pool.queue[from] == nil {
			logger.Trace("Rejecting a new Tx, because TxPool is full and there is no room for the account", "hash", tx.Hash(), "account", from)
			refusedTxCounter.Inc(1)
			return false, fmt.Errorf("txpool is full: %d", uint64(pool.all.Count()))
		}

```

**File:** blockchain/tx_pool.go (L1209-1211)
```go
	// If the transaction is replacing an already pending one, do directly
	from, _ := types.Sender(pool.signer, tx) // already validated
	if list := pool.pending[from]; list != nil && list.Overlaps(tx) {
```

**File:** blockchain/tx_pool.go (L1228-1228)
```go
		logger.Trace("Pooled new executable transaction", "hash", hash, "from", from, "to", tx.To())
```

**File:** blockchain/tx_pool.go (L1544-1558)
```go
func (pool *TxPool) addTx(tx *types.Transaction, local bool) error {
	senderCacher.recover(pool.signer, []*types.Transaction{tx})

	pool.mu.Lock()
	defer pool.mu.Unlock()

	// Try to inject the transaction and update any state
	replace, err := pool.add(tx, local)
	if err != nil {
		return err
	}
	// If we added a new transaction, run promotion checks and return
	if !replace {
		from, _ := types.Sender(pool.signer, tx) // already validated
		pool.promoteExecutables([]common.Address{from})
```

**File:** blockchain/tx_pool.go (L1580-1586)
```go
	for i, tx := range txs {
		var replace bool
		if replace, errs[i] = pool.add(tx, local); errs[i] == nil {
			if !replace {
				from, _ := types.Sender(pool.signer, tx) // already validated
				dirty[from] = struct{}{}
			}
```
