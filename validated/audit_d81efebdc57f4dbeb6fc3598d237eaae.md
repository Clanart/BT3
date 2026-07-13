### Title
EIP-7702 Authorization with `chainId = 0` Enables Cross-Chain Replay of Code Delegation — (`File: x/evm/keeper/set_code_authorizations.go`)

### Summary

Ethermint's `validateAuthorization` explicitly permits EIP-7702 `SetCodeAuthorization` entries whose `ChainID` field is zero. Because the authorization signature is computed over `keccak256(0x05 || rlp([chain_id, address, nonce]))`, a zero chain ID produces a digest that is identical on every EVM chain. Any valid authorization signed with `chainId = 0` on one Ethermint-based chain (e.g., Cronos, Evmos) can be lifted and replayed verbatim on any other Ethermint chain where the authority's nonce matches, installing an attacker-chosen delegate contract on the victim's EOA without the victim's consent on that chain.

### Finding Description

`validateAuthorization` in `x/evm/keeper/set_code_authorizations.go` performs the chain-ID check as:

```go
if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

When `auth.ChainID` is zero the entire chain-ID guard is bypassed. [1](#0-0) 

The remaining checks are a nonce equality test and a signature recovery:

```go
authority, err = auth.Authority()   // ecrecover over (chainId=0, address, nonce)
...
if have := stateDB.GetNonce(authority); have != auth.Nonce {
    return authority, core.ErrAuthorizationNonceMismatch
}
``` [2](#0-1) 

Neither check is chain-specific when `chainId = 0`. The EIP-7702 signing hash is `keccak256(0x05 || rlp([0, delegateAddr, nonce]))`, which is identical across all EVM chains. A valid `(v, r, s)` tuple recovered on chain A is therefore cryptographically valid on chain B, C, … as long as the authority's nonce matches.

Once `applyAuthorization` succeeds, `setAuthorizationDelegation` unconditionally writes the attacker-chosen delegate code to the authority's account:

```go
stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
``` [3](#0-2) 

The durable-authorization path in `ApplyMessageWithConfig` then commits this code change to the persistent store even if the outer EVM call reverts: [4](#0-3) 

The ante handler performs no per-authorization chain-ID check beyond what `validateAuthorization` does; it only verifies that the authorization list is non-empty and that the outer transaction's `To` is non-nil: [5](#0-4) 

### Impact Explanation

An attacker who observes a `chainId = 0` EIP-7702 authorization on chain A (e.g., from a dApp that generates chain-agnostic authorizations, or from a victim who explicitly chose `chainId = 0`) can replay the exact same `(v, r, s, nonce, delegateAddr)` tuple on chain B by wrapping it in a new outer EIP-7702 transaction. If the victim's nonce on chain B equals `auth.Nonce` (trivially true for fresh accounts with nonce 0, or for accounts with identical transaction histories across chains), the delegation is installed. The attacker then calls the victim's EOA on chain B; the EVM executes the attacker-controlled delegate, which can transfer the victim's entire EVM-denom balance to the attacker. This is unauthorized fund theft via unauthorized account/code mutation — matching the High/Critical allowed impact.

### Likelihood Explanation

- **Entry path**: Fully unprivileged. The attacker submits a standard EIP-7702 transaction via `eth_sendRawTransaction` or the Cosmos broadcast endpoint.
- **Nonce precondition**: Nonce 0 is the most common case (new accounts). Many users create fresh EOAs on each Ethermint chain with no prior transactions, making nonce = 0 the default. Accounts that mirror activity across chains (e.g., airdrop recipients, bridge users) also frequently share nonces.
- **Signature availability**: `chainId = 0` authorizations are observable on-chain once included in any block. Wallets and dApps that generate chain-agnostic authorizations (for multi-chain deployment scripts, hardware-wallet flows, or cross-chain protocols) produce exactly this artifact.
- **No privileged role required**: The attacker only needs to observe the authorization and submit a transaction.

### Recommendation

1. **Reject `chainId = 0` authorizations at the ante-handler level** for Ethermint deployments that do not explicitly opt in to cross-chain delegation. Add a check in `ValidateEthBasic` or in `validateAuthorization` itself:
   ```go
   if auth.ChainID.IsZero() {
       return authority, core.ErrAuthorizationWrongChainID
   }
   ```
2. If cross-chain authorizations must be supported, document the replay risk prominently and require integrators to use chain-specific nonces or a separate domain-separation mechanism.
3. Align with go-ethereum's own stance: geth's `stateTransition.go` also allows `chainId = 0` per spec, but Ethermint's multi-chain deployment context makes this significantly more dangerous than on a single canonical chain.

### Proof of Concept

**Setup**: Two Ethermint chains, chain A (`chainId = 9000`) and chain B (`chainId = 9001`). Victim `V` has nonce 0 on both chains.

1. **Chain A**: Attacker constructs an EIP-7702 transaction where the authorization list contains:
   ```
   auth = { chainId: 0, address: <malicious_delegate>, nonce: 0, v/r/s: signed_by_V }
   ```
   The victim is tricked into signing this (e.g., via a dApp that claims `chainId = 0` means "this chain only"). The transaction is broadcast and included on chain A. `V`'s nonce on chain A becomes 1.

2. **Chain B**: Attacker copies the identical `auth` tuple into a new outer EIP-7702 transaction (with a fresh outer signature from the attacker's own key, paying gas). Broadcasts to chain B.

3. **`validateAuthorization` on chain B**:
   - `auth.ChainID.IsZero()` → `true` → chain-ID check skipped. [6](#0-5) 
   - `auth.Authority()` recovers `V` (same signature, same digest). [7](#0-6) 
   - `stateDB.GetNonce(V) == 0 == auth.Nonce` → nonce check passes. [8](#0-7) 
   - `setAuthorizationDelegation` installs `<malicious_delegate>` as `V`'s code on chain B. [3](#0-2) 

4. **Drain**: Attacker calls `V`'s address on chain B. The EVM executes `<malicious_delegate>` in `V`'s context, transferring `V`'s entire balance to the attacker.

### Citations

**File:** x/evm/keeper/set_code_authorizations.go (L16-19)
```go
	// Verify chain ID is null or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
		return authority, core.ErrAuthorizationWrongChainID
	}
```

**File:** x/evm/keeper/set_code_authorizations.go (L24-41)
```go
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
```

**File:** x/evm/keeper/set_code_authorizations.go (L83-84)
```go
	// Otherwise install delegation to auth.Address.
	stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
```

**File:** x/evm/keeper/state_transition.go (L502-513)
```go
			if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
				durableStateDB := statedb.NewWithParams(*cfg.DurableSetCodeAuthorizationCtx, k, cfg.TxConfig, cfg.Params.EvmDenom)
				for _, va := range validAuths {
					// Replay the already-validated effects; this cannot fail, so it
					// mirrors the main loop's skip-on-invalid behavior without ever
					// turning an EVM-level outcome into a cosmos-level tx error.
					k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
				}
				if err := durableStateDB.Commit(); err != nil {
					return nil, errorsmod.Wrap(err, "failed to commit durable EIP-7702 authorization stateDB")
				}
			}
```

**File:** ante/interfaces/setup.go (L132-140)
```go
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
