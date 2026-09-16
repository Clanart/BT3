### Title
EIP-7702 `SetCodeAuthorization` tuples can be extracted from a pending transaction and front-run by an unrelated sender to hijack the authority's account before its intended atomic initialization — ([File: blockchain/state_transition.go])

### Summary
Kaia's EIP-7702 implementation (`TxTypeEthereumSetCode`) accepts an `AuthorizationList` of `SetCodeAuthorization` tuples that are signed independently of the transaction that carries them, exactly like an ERC-20 `permit` signature. Anyone who observes such a tuple in the mempool can copy it into their own transaction and apply the delegation themselves, decoupling "who signed the authorization" from "who gets to act first on the freshly-delegated account" — the same root cause as the BakerFi `pullTokensWithPermit` frontrunning issue.

### Finding Description
A `SetCodeAuthorization`'s signature only covers `(chainID, address, nonce)`: [1](#0-0) 

It is *not* bound to the transaction's sender, `to`, `value`, or `data`. Validation in `StateTransition.validateAuthorization` only checks chain ID, nonce overflow, ECDSA validity, existing code/delegation state, and that the authority's current on-chain nonce matches: [2](#0-1) 

`applyAuthorization` then unconditionally mutates the authority's account (increments nonce, installs delegation) regardless of who submitted the enclosing transaction: [3](#0-2) 

These authorizations are processed for *every* entry in `msg.AuthList()` before the outer transaction's call executes, and errors on individual tuples are silently swallowed: [4](#0-3) 

A typical EIP-7702 wallet-setup flow signs an authorization delegating the EOA to a smart-account implementation `W`, and atomically calls `authority.initialize(owner)` in the *same* transaction so nobody else can claim ownership of the freshly-delegated code. Because the authorization tuple is visible in the mempool as soon as the victim's transaction is broadcast, and it is not scoped to a specific transaction or call, an attacker can:
1. Extract the tuple `(chainID, W, nonce, v, r, s)` from the victim's pending transaction.
2. Submit their own `TxTypeEthereumSetCode`-capable transaction embedding the identical tuple, which advances the authority's nonce and installs the `W` delegation via `SetCodeToEOA`.
3. In the same attacker transaction, call `authority.initialize(attacker)` (msg.sender of that call is the attacker's own EOA, not the victim), claiming ownership of the now-delegated account before the victim's own initialize call executes.
4. The victim's original transaction's authorization entry now fails `ErrAuthorizationNonceMismatch` (silently skipped) and, if it also called `initialize()` under the assumption it would be the first caller, that call either reverts or succeeds against attacker-controlled state, resulting in loss of control/funds routed through the account.

This is structurally identical to the referenced BakerFi report: a reusable, unauthenticated-as-to-sender signature (permit / SetCodeAuthorization) that lets any third party act on behalf of the signer ahead of the signer's own intended transaction, turning atomic "authorize + use" patterns into a race the victim always loses.

### Impact Explanation
An attacker who wins this race can seize control of a delegated smart-account (become its "owner"/authorized signer within `W`'s storage layout at the authority's address), enabling subsequent unauthorized draining of any KAIA/tokens sent to or held by that address, and/or blocking the legitimate owner from ever initializing their own account — a concrete unauthorized value-movement/account-takeover impact reachable by any unprivileged transaction sender who is simply watching the public mempool.

### Likelihood Explanation
This requires only observing a pending `TxTypeEthereumSetCode` transaction in the mempool (or via an RPC node) and submitting a competing transaction with a higher gas price/tip — no privileged access, validator collusion, or protocol-level bug is needed. Any dApp or wallet vendor building "delegate + initialize" flows on top of Kaia's EIP-7702 support is exposed. Likelihood is elevated wherever wallet-initialization patterns assume atomicity between delegation and first-call ownership.

### Recommendation
- Document and strongly warn integrators that `SetCodeAuthorization` tuples are not sender-bound and must never be relied upon for atomic "delegate-then-initialize" patterns; the delegated contract `W`'s initializer must itself authenticate the caller (e.g., require `msg.sender == authority` via signature, or use a commit-reveal/two-step ownership claim) rather than trusting "first caller after delegation."
- Consider chain-level mitigations: e.g., restrict processing of an authorization item to transactions whose `tx.origin`/validated sender equals the authority itself, or bind authorizations to the transaction hash of the intended enclosing transaction, mirroring recommended fixes for permit-front-running (do not accept a third-party-submitted authorization as sufficient to service an unrelated call in the same transaction without additional binding).

### Proof of Concept
1. Victim signs `SetCodeAuthorization{ChainID, Address: W, Nonce: n}` and broadcasts `txVictim = {to: authority, authorizationList: [auth], data: initialize(victim)}`.
2. Attacker observes `txVictim` in the mempool, extracts `auth`, and broadcasts `txAttacker = {to: authority, authorizationList: [auth], data: initialize(attacker)}` with a higher gas price.
3. `txAttacker` is mined first: `validateAuthorization`/`applyAuthorization` (blockchain/state_transition.go:732-793) install the `W` delegation and increment `authority`'s nonce; the subsequent call in the same transaction executes `initialize(attacker)` against the now-delegated code, with `attacker` becoming the recorded owner in `W`'s storage at `authority`.
4. `txVictim` is mined next: its authorization entry now fails `ErrAuthorizationNonceMismatch` and is silently skipped (blockchain/state_transition.go:588-592), while `initialize(victim)` executes against attacker-owned storage — either reverting (denial of the victim's own account) or, depending on `W`'s logic, allowing the attacker's prior claim to persist, resulting in a hijacked account.

### Citations

**File:** blockchain/types/tx_internal_data_ethereum_set_code.go (L479-485)
```go
func (a *SetCodeAuthorization) sigHash() common.Hash {
	return prefixedRlpHash(0x05, []any{
		a.ChainID,
		a.Address,
		a.Nonce,
	})
}
```

**File:** blockchain/state_transition.go (L576-593)
```go
	// skip when creating a new contract
	if !contractCreation {
		// Unlike other transaction types, where the sender nonce is incremented in msg.Execute(),
		// SetCodeTx's sender nonce should be incremented before processing AuthList.
		if msg.Type() == types.TxTypeEthereumSetCode {
			// Increment the nonce for the next transaction.
			// Note: EIP-7702 authorizations can also modify the nonce. We perform
			// this update first to ensure correct validation of authorization nonces.
			st.state.IncNonce(msg.ValidatedSender())
		}

		// Apply EIP-7702 authorizations.
		if msg.AuthList() != nil {
			for _, auth := range msg.AuthList() {
				// Note errors are ignored, we simply skip invalid authorizations here.
				st.applyAuthorization(&auth, rules)
			}
		}
```

**File:** blockchain/state_transition.go (L732-759)
```go
func (st *StateTransition) validateAuthorization(auth *types.SetCodeAuthorization) (authority common.Address, err error) {
	// Verify chain ID is 0 or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(st.evm.ChainConfig().ChainID) != 0 {
		return authority, ErrAuthorizationWrongChainID
	}
	// Limit nonce to 2^64-1 per EIP-2681.
	if auth.Nonce+1 < auth.Nonce {
		return authority, ErrAuthorizationNonceOverflow
	}
	// Validate signature values and recover authority.
	authority, err = auth.Authority()
	if err != nil {
		return authority, fmt.Errorf("%w: %v", ErrAuthorizationInvalidSignature, err)
	}
	// Check the authority account
	//  1) doesn't have code or has existing delegation
	//  2) matches the auth's nonce
	//
	// Note it is added to the access list even if the authorization is invalid.
	st.state.AddAddressToAccessList(authority)
	code := st.state.GetCode(authority)
	if _, ok := types.ParseDelegation(code); len(code) != 0 && !ok {
		return authority, ErrAuthorizationDestinationHasCode
	}
	if have := st.state.GetNonce(authority); have != auth.Nonce {
		return authority, ErrAuthorizationNonceMismatch
	}
	return authority, nil
```

**File:** blockchain/state_transition.go (L762-793)
```go
func (st *StateTransition) applyAuthorization(auth *types.SetCodeAuthorization, rules params.Rules) (err error) {
	authority, err := st.validateAuthorization(auth)
	if err != nil {
		return err
	}

	// If the account already exists in state, refund the new account cost
	// charged in the initrinsic calculation.
	if st.state.Exist(authority) {
		// If the account is not AccountKeyTypeLegacy, setcode is not allowed.
		accountKeyType := st.state.GetKey(authority).Type()
		if !accountKeyType.IsLegacyAccountKey() {
			return fmt.Errorf("%w: %v", ErrAuthorizationNotAllowAccountKeyType, accountKeyType)
		}
		st.state.AddRefund(params.CallNewAccountGas - params.TxAuthTupleGas)
	}

	// Update nonce and account code.
	st.state.IncNonce(authority)
	delegation := types.AddressToDelegation(auth.Address)
	if common.EmptyAddress(auth.Address) {
		// Delegation to zero address means clear.
		st.state.SetCodeToEOA(authority, []byte{}, rules)
		return nil
	}

	// Otherwise install delegation to auth.Address.
	// We treat EOA and SCA as separate objects and therefore need to use
	// distinct methods.
	st.state.SetCodeToEOA(authority, delegation, rules)

	return nil
```
