### Title
EIP-7702 Authorization Replay Across Chains via Zero Chain ID Bypass - (File: `x/evm/keeper/set_code_authorizations.go`)

### Summary
Ethermint's EIP-7702 authorization validation explicitly permits authorizations signed with a zero chain ID (`ChainID == 0`). This is intentional per the EIP-7702 spec to allow "chain-agnostic" delegations, but it creates a direct replay attack surface: a single signed authorization can be replayed on any Ethermint-based chain (or any EVM chain) that accepts zero-chain-ID authorizations, allowing an attacker to install arbitrary delegation code on a victim's EOA account on chains the victim never intended to authorize.

### Finding Description

In `validateAuthorization`, the chain ID check is:

```go
if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
``` [1](#0-0) 

The condition only rejects authorizations whose chain ID is **non-zero and wrong**. An authorization signed with `ChainID = 0` passes this check unconditionally on every chain. The same 65-byte `(v, r, s)` signature over `(chainID=0, address=X, nonce=N)` is valid on Ethermint chain A, chain B, chain C, and any other EVM chain that follows the same rule.

The authorization is then applied via `setAuthorizationDelegation`, which writes delegation code to the authority's account:

```go
stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
``` [2](#0-1) 

The nonce check (`have != auth.Nonce`) provides partial protection — the authorization is only valid when the authority's on-chain nonce matches `auth.Nonce`. However, if the victim's account has the same nonce on multiple chains (e.g., a fresh account with nonce 0 on all chains), the authorization is replayable across all of them.

The entry path is a standard unprivileged `eth_sendRawTransaction` / `MsgEthereumTx` of type `SetCodeTxType` (EIP-7702). The ante handler validates the outer transaction's chain ID via `VerifyEthSig` using `ethtypes.MakeSigner`, but does **not** validate the chain IDs of individual authorizations in the auth list:

```go
// Check that EIP-7702 authorization list signatures are well formed.
if tx.SetCodeAuthorizations() != nil {
    if tx.To() == nil { ... }
    if len(tx.SetCodeAuthorizations()) == 0 { ... }
}
``` [3](#0-2) 

No per-authorization chain ID validation occurs in the ante handler. The only validation happens at execution time in `validateAuthorization`, which accepts zero chain IDs.

### Impact Explanation

An attacker who obtains a victim's zero-chain-ID EIP-7702 authorization (e.g., from a transaction on chain A) can replay it on chain B by embedding it in a new `SetCodeTx` outer transaction (signed by the attacker, not the victim). If the victim's nonce on chain B matches `auth.Nonce`, the delegation is installed. This:

- Installs attacker-controlled delegation code on the victim's EOA on chain B, redirecting all calls to the victim's address to an attacker-controlled contract.
- Enables the attacker to drain the victim's EVM-denom balance on chain B by calling the victim's address (now delegated to a malicious contract) and executing a `SELFDESTRUCT` or transfer.
- Constitutes unauthorized account/code mutation and unauthorized balance transfer — matching the **High** impact category: "EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation."

### Likelihood Explanation

- Zero-chain-ID authorizations are a known pattern in EIP-7702 tooling (some wallets and libraries generate them for "universal" delegations).
- Any Ethermint-based ecosystem with multiple chains (e.g., Cronos mainnet + testnet, or two Cosmos chains sharing the same EVM tooling) is directly exposed.
- The attacker only needs to observe a zero-chain-ID authorization on one chain (from mempool or committed block) and submit a new outer transaction on another chain. No privileged access is required.
- The nonce constraint is a partial mitigant but not a reliable one: fresh accounts start at nonce 0 on all chains, and the nonce check uses the stateDB nonce at execution time, which an attacker can arrange to match by timing the replay.

### Recommendation

1. **Reject zero chain ID authorizations in production**: Add a check in `validateAuthorization` to reject authorizations where `auth.ChainID.IsZero()`, unless the deployment explicitly opts into cross-chain delegations with full awareness of the replay risk.

```go
if auth.ChainID.IsZero() || auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

2. **Add ante-handler pre-screening**: In `ValidateEthBasic` (`ante/interfaces/setup.go`), iterate over `tx.SetCodeAuthorizations()` and reject any authorization with a zero chain ID or a chain ID that does not match the current chain, failing the transaction before it reaches execution.

3. **Document the risk**: If zero-chain-ID authorizations must be supported for compatibility, document clearly that they are replayable across all chains and that users must understand the implications.

### Proof of Concept

1. Alice signs an EIP-7702 authorization on Ethermint chain A (chain ID 9000) with `ChainID = 0`, `address = MaliciousContract`, `nonce = 0`. This is submitted in a `SetCodeTx` on chain A.
2. Bob (attacker) observes the authorization `(v, r, s)` from the mempool or committed block on chain A.
3. Bob constructs a new `SetCodeTx` on Ethermint chain B (chain ID 9001), embedding Alice's `(v, r, s)` authorization with `ChainID = 0`, `address = MaliciousContract`, `nonce = 0`. Bob signs the outer transaction with his own key.
4. On chain B, `validateAuthorization` is called: `auth.ChainID.IsZero()` is true, so the chain ID check is skipped. Alice's nonce on chain B is 0 (fresh account), so the nonce check passes. The authorization is applied: Alice's account on chain B now has delegation code pointing to `MaliciousContract`.
5. Bob calls Alice's address on chain B. The call is redirected to `MaliciousContract`, which transfers Alice's entire EVM-denom balance to Bob. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** x/evm/keeper/set_code_authorizations.go (L15-43)
```go
func (k *Keeper) validateAuthorization(auth *types.SetCodeAuthorization, stateDB vm.StateDB) (authority common.Address, err error) {
	// Verify chain ID is null or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
		return authority, core.ErrAuthorizationWrongChainID
	}
	// Limit nonce to 2^64-1 per EIP-2681.
	if auth.Nonce+1 < auth.Nonce {
		return authority, core.ErrAuthorizationNonceOverflow
	}
	// Validate signature values and recover authority.
	authority, err = auth.Authority()
	if err != nil {
		return authority, fmt.Errorf("%w: %v", core.ErrAuthorizationInvalidSignature, err)
	}
	// Check the authority account
	//  1) doesn't have code or has exisiting delegation
	//  2) matches the auth's nonce
	//
	// Note it is added to the access list even if the authorization is invalid.
	stateDB.AddAddressToAccessList(authority)
	code := stateDB.GetCode(authority)
	if _, ok := types.ParseDelegation(code); len(code) != 0 && !ok {
		return authority, core.ErrAuthorizationDestinationHasCode
	}
	if have := stateDB.GetNonce(authority); have != auth.Nonce {
		return authority, core.ErrAuthorizationNonceMismatch
	}
	return authority, nil
}
```

**File:** x/evm/keeper/set_code_authorizations.go (L72-85)
```go
// setAuthorizationDelegation writes the nonce bump and delegation code for a
// validated authorization.
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
	// Update nonce and account code.
	stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
	if auth.Address == (common.Address{}) {
		// Delegation to zero address means clear.
		stateDB.SetCode(authority, nil, tracing.CodeChangeAuthorizationClear)
		return
	}

	// Otherwise install delegation to auth.Address.
	stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
}
```

**File:** ante/interfaces/setup.go (L126-140)
```go
		if !allowUnprotectedTxs && !tx.Protected() {
			return errorsmod.Wrapf(
				errortypes.ErrNotSupported,
				"rejected unprotected Ethereum transaction. Please EIP155 sign your transaction to protect it against replay-attacks")
		}

		// Check that EIP-7702 authorization list signatures are well formed.
		if tx.SetCodeAuthorizations() != nil {
			if tx.To() == nil {
				return errorsmod.Wrapf(errortypes.ErrInvalidRequest, "EIP-7702 set code transaction cannot be contract creation (sender %v)", msgEthTx.From)
			}
			if len(tx.SetCodeAuthorizations()) == 0 {
				return errorsmod.Wrapf(errortypes.ErrInvalidRequest, "EIP-7702 authorization list cannot be empty (sender %v)", msgEthTx.From)
			}
		}
```
