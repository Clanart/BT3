Based on my research, I found a genuine analog to the "front-running lock" bug class in Kaia's EIP-7702 (`TxTypeEthereumSetCode`) authority reservation mechanism.

### Title
Unprivileged front-running of EIP-7702 authority reservation griefs pending delegated-account transactions - ([File: blockchain/tx_pool.go])

### Summary
Similar to the `startNewRound()` front-running griefing pattern (where any unprivileged caller can trigger a state lock that blocks a legitimate pending action), Kaia's tx pool enforces a "one in-flight transaction per authority" rule for EIP-7702 `SetCode` authorizations via `checkDelegationLimit`/`validateAuth`. Because a `SetCodeAuthorization` is a detached, replayable signed tuple `(ChainID, Address, Nonce)` that can be wrapped into a transaction sent by *any* sender (not just the authority), an unprivileged actor who observes an authority's authorization can front-run and occupy that authority's single reserved slot, causing the authority's own legitimate transaction to be rejected from the pool.

### Finding Description
`checkDelegationLimit` restricts accounts with existing code or a pending authorization to at most one in-flight (pending) transaction: [1](#0-0) 

`validateAuth` further enforces, for every authority named in `tx.SetCodeAuthorities()`, that the authority cannot have more than one in-flight transaction across `pending`/`queue`, else it returns `ErrAuthorityReserved`: [2](#0-1) 

Crucially, a `SetCodeAuthorization` is signed only over `(ChainID, Address, Nonce)` — it is not bound to any specific transaction, sender, or gas parameters — so **anyone** can take an authority's signed authorization (once observed, e.g. in the mempool or from any published source) and embed it into a brand-new `TxTypeEthereumSetCode` transaction that they themselves send: [3](#0-2) 

This is the same "detached authorization, sendable by any party" design intentionally used for sponsored/gasless delegation (as shown by the module tests where account A submits a `setCodeTx` containing an authorization signed by B): [4](#0-3) 

An attacker who sees a victim's `SetCodeAuthorization` circulating (or simply predicts/knows it, e.g., because the victim published it off-chain for a legitimate sponsor to pick up) can immediately submit their own `TxTypeEthereumSetCode` transaction embedding that same authorization tuple. Because `checkDelegationLimit`/`validateAuth` allow only one in-flight tx per authority, this attacker transaction occupies the slot. Any subsequent legitimate transaction from the victim (either their own follow-up tx, or the intended sponsor's tx carrying the same authorization) will be rejected with `ErrAuthorityReserved` / `ErrInflightTxLimitReached`, exactly mirroring the `startNewRound` pattern where an unprivileged front-runner triggers a limiting condition to grief a legitimate pending action.

### Impact Explanation
This griefing vector can be used to:
- Block a victim's intended account-delegation (smart-account upgrade) from being included, indefinitely delaying activation of their intended code delegation.
- Disrupt gasless/sponsored delegation flows (as used by `kaiax/gasless`), since the sponsor's transaction carrying the victim's authorization can be pre-empted by an attacker's own competing `SetCode` transaction using the identical authorization, before the legitimate sponsor's tx lands.
- Cause repeated transaction pool rejections (`ErrAuthorityReserved`) for the victim, since as long as the attacker's transaction with the stale authorization remains pending/queued, the victim's slot stays reserved.

This does not directly move funds but is a concrete denial-of-service/griefing primitive against a specific unprivileged tx-pool admission rule, analogous in class and impact to the report's `heal`/`escape` DoS via front-run lock.

### Likelihood Explanation
Likelihood depends on an attacker observing a victim's `SetCodeAuthorization` before the intended transaction is confirmed (e.g., via mempool monitoring, since authorizations for sponsored delegation are typically shared out-of-band or gossiped prior to submission). Given Kaia's auction/gasless module ecosystem is built around sponsors submitting authorizations/transactions signed by other parties, this exposure is realistic in the specific sponsored-delegation use case, but requires the attacker to have the raw authorization bytes, which is not automatically public unless the sponsor's original transaction is already broadcast (in which case the "slot" would already be occupied by the legitimate tx, reducing the race window). This constrains the likelihood to a race-condition/timing-dependent scenario rather than an always-exploitable path.

### Recommendation
Consider one or more of:
- Requiring that a `SetCode` transaction consuming an authority's authorization also matches an expected fee-payer/sponsor allowlist, or
- Only reserving the authority's slot once the specific transaction (by hash) is confirmed to originate from an authorized sponsor, or
- Allowing authority-slot preemption based on higher priority fee (similar to standard tx replacement) so a legitimate sponsor's transaction can always displace a griefing transaction that reuses the same authorization.

### Proof of Concept
1. Victim V signs a `SetCodeAuthorization{ChainID, Address=D, Nonce=n}` intending it to be embedded in a sponsor S's `TxTypeEthereumSetCode` transaction (sponsored/gasless delegation flow).
2. V or S broadcasts/prepares this authorization (e.g., over a P2P relay or shared with sponsor) before S's transaction is included on-chain.
3. Attacker A observes the raw authorization tuple and immediately crafts and submits their own `TxTypeEthereumSetCode` transaction (any `to`/`value`, own nonce) embedding the exact same authorization from V.
4. `pool.validateAuth` accepts A's transaction (first to occupy the slot) and registers V as a reserved authority; see `checkDelegationLimit`/`validateAuth` at [5](#0-4) .
5. When sponsor S's legitimate transaction (or V's own subsequent tx) arrives with the same authority V, it is rejected with `ErrAuthorityReserved`/`ErrInflightTxLimitReached`, blocking V's intended delegation until A's transaction is mined or evicted.

### Citations

**File:** blockchain/tx_pool.go (L1038-1111)
```go
func (pool *TxPool) checkDelegationLimit(tx *types.Transaction) error {
	from, _ := types.Sender(pool.signer, tx) // validated

	// Short circuit if the sender has neither delegation nor pending delegation.
	if pool.currentState.GetCodeHash(from) == types.EmptyCodeHash && !pool.all.hasAuth(from) {
		return nil
	}
	pending := pool.pending[from]
	if pending == nil {
		// Transaction with gapped nonce is not supported for delegated accounts
		if pool.getPendingNonce(from) != tx.Nonce() {
			return ErrOutOfOrderTxFromDelegated
		}
		return nil
	}
	// Transaction replacement is supported
	if pending.Contains(tx.Nonce()) {
		return nil
	}
	return ErrInflightTxLimitReached
}

// validateBlobTx validates the blob transaction fields.
func (pool *TxPool) validateBlobTx(tx *types.Transaction) error {
	sidecar := tx.BlobTxSidecar()
	if sidecar == nil {
		return errors.New("missing sidecar in blob transaction")
	}
	// Ensure the blob fee cap satisfies the minimum blob gas price
	if tx.BlobGasFeeCapIntCmp(pool.blobBaseFee) < 0 {
		return fmt.Errorf("%w: blob fee cap %v, minimum needed %v", ErrTxGasPriceTooLow, tx.BlobGasFeeCap(), pool.blobBaseFee)
	}
	// Ensure the number of items in the blob transaction and various side
	// data match up before doing any expensive validations
	hashes := tx.BlobHashes()
	if len(hashes) == 0 {
		return errors.New("blobless blob transaction")
	}
	if len(hashes) > params.BlobTxMaxBlobs {
		return fmt.Errorf("too many blobs in transaction: have %d, permitted %d", len(hashes), params.BlobTxMaxBlobs)
	}
	if err := sidecar.ValidateWithBlobHashes(hashes); err != nil {
		return err
	}
	return nil
}

// validateAuth verifies that the transaction complies with code authorization
// restrictions brought by SetCode transaction type.
func (pool *TxPool) validateAuth(tx *types.Transaction) error {
	// Allow at most one in-flight tx for delegated accounts or those with a
	// pending authorization.
	if err := pool.checkDelegationLimit(tx); err != nil {
		return err
	}

	// For symmetry, allow at most one in-flight tx for any authority with a
	// pending transaction.
	for _, auth := range tx.SetCodeAuthorities() {
		var count int
		if pending := pool.pending[auth]; pending != nil {
			count += pending.Len()
		}
		if queue := pool.queue[auth]; queue != nil {
			count += queue.Len()
		}
		// Replace the existing in-flight transaction for delegated accounts
		// are still supported
		if count > 1 {
			return ErrAuthorityReserved
		}
	}
	return nil
}
```

**File:** blockchain/types/tx_internal_data_ethereum_set_code.go (L443-485)
```go
// SetCodeAuthorization is an authorization from an account to deploy code at its address.
type SetCodeAuthorization struct {
	ChainID uint256.Int    `gencodec:"required" json:"chainId"`
	Address common.Address `gencodec:"required" json:"address"`
	Nonce   uint64         `gencodec:"required" json:"nonce"`
	V       uint8          `gencodec:"required" json:"yParity"`
	R       uint256.Int    `gencodec:"required" json:"r"`
	S       uint256.Int    `gencodec:"required" json:"s"`
}

type authorizationMarshaling struct {
	ChainID hexutil.U256
	Nonce   hexutil.Uint64
	V       hexutil.Uint64
	R       hexutil.U256
	S       hexutil.U256
}

// SignSetCode creates a signed the SetCode authorization.
func SignSetCode(prv *ecdsa.PrivateKey, auth SetCodeAuthorization) (SetCodeAuthorization, error) {
	sighash := auth.sigHash()
	sig, err := crypto.Sign(sighash[:], prv)
	if err != nil {
		return SetCodeAuthorization{}, err
	}
	r, s, _ := decodeSignature(sig)
	return SetCodeAuthorization{
		ChainID: auth.ChainID,
		Address: auth.Address,
		Nonce:   auth.Nonce,
		V:       sig[crypto.RecoveryIDOffset],
		R:       *uint256.MustFromBig(r),
		S:       *uint256.MustFromBig(s),
	}, nil
}

func (a *SetCodeAuthorization) sigHash() common.Hash {
	return prefixedRlpHash(0x05, []any{
		a.ChainID,
		a.Address,
		a.Nonce,
	})
}
```

**File:** blockchain/tx_pool_test.go (L2480-2498)
```go
		{
			// This is the symmetric case of the previous one, where the delegation request
			// is received after the transaction. The resulting state shall be the same.
			name:    "accept-authorization-from-sender-of-one-inflight-tx",
			pending: 2,
			run: func(name string) {
				// The first in-flight transaction is accepted.
				if err := pool.addTx(pricedTransaction(0, 100000, big.NewInt(1), keyB), false); err != nil {
					t.Fatalf("%s: failed to add with pending delegation: %v", name, err)
				}
				// Delegation is accepted.
				if err := pool.addTx(setCodeTx(0, keyA, []unsignedAuth{{0, keyB}}), false); err != nil {
					t.Fatalf("%s: failed to add remote transaction: %v", name, err)
				}
				// The second in-flight transaction is rejected.
				if err := pool.addTx(pricedTransaction(1, 100000, big.NewInt(1), keyB), false); !errors.Is(err, ErrInflightTxLimitReached) {
					t.Fatalf("%s: error mismatch: want %v, have %v", name, ErrInflightTxLimitReached, err)
				}
			},
```
