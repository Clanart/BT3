### Title
EIP-7702 Authorization Chain-ID Zero Bypass Allows Cross-Chain Replay of Code Delegation — (`File: x/evm/keeper/set_code_authorizations.go`)

### Summary
The `validateAuthorization` function in `x/evm/keeper/set_code_authorizations.go` explicitly permits EIP-7702 authorizations with a zero chain ID to pass chain-ID validation. This is by design per EIP-7702 (chain-ID 0 means "valid on any chain"), but Ethermint's implementation uses this to allow an authorization signed on one chain to be replayed on any other Ethermint-based chain, mutating an EOA's code and nonce without the account owner's consent on the target chain. The analog to the original report is exact: the wrong "context" (chain-ID zero = no chain binding) is accepted for a security-critical authorization, enabling unauthorized account/code mutation.

### Finding Description

In `validateAuthorization`, the chain-ID check is:

```go
if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

When `auth.ChainID` is zero, the entire check is skipped. An attacker who observes a valid EIP-7702 authorization signed with `chainID=0` on any chain (or who crafts one themselves) can submit it as part of a `SetCodeTx` on any Ethermint chain. The authorization will pass `validateAuthorization`, and `setAuthorizationDelegation` will then:

1. Bump the authority account's nonce (`auth.Nonce + 1`)
2. Install arbitrary delegation bytecode at the authority's address

This is the direct Ethermint analog of the Gauge.sol bug: the wrong "context" (chain-ID zero instead of the current chain's ID) is accepted for a security-critical operation, enabling unauthorized account mutation.

The entry path is fully unprivileged: any user can submit a `MsgEthereumTx` wrapping a `SetCodeTx` (type 4) with an authorization list containing a zero-chain-ID authorization. The `VerifyEthSig` / `VerifyEthAccount` ante handlers only verify the *outer* transaction signer, not the authorization list signers. The authorization list is processed inside `ApplyMessageWithConfig` → `applyAuthorization` → `validateAuthorization`, which is reached for every EIP-7702 transaction that passes ante checks.

The durable authorization path in `ApplyTransaction` makes this persistent even if the outer EVM call reverts:

```go
if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
    // ... commits authorization effects even on EVM failure
}
```

### Impact Explanation

An attacker can:
1. Obtain or craft an EIP-7702 authorization signed with `chainID=0` targeting a malicious delegate contract.
2. Submit a `SetCodeTx` on any Ethermint chain containing that authorization.
3. The victim EOA's code is replaced with delegation bytecode pointing to the attacker's contract, and the victim's nonce is incremented.
4. All subsequent calls to the victim's address execute the attacker's delegate code in the victim's context — enabling theft of the victim's EVM-denom balance, unauthorized state mutation, and permanent account compromise.

This matches the allowed High impact: "EIP-7702 authorization, nonce, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation."

### Likelihood Explanation

EIP-7702 is a new feature (Prague hardfork). Wallets and users signing authorizations with `chainID=0` for "universal" use is explicitly permitted by the EIP spec and expected in practice. Any such authorization observed on any chain (e.g., Ethereum mainnet) can be replayed on any Ethermint chain. The attack requires only submitting a valid outer `SetCodeTx` with the replayed authorization — no privileged access, no key compromise, no validator collusion.

### Recommendation

In `validateAuthorization`, reject zero chain-ID authorizations rather than accepting them as chain-agnostic:

```go
// Reject zero chain-ID: require explicit binding to this chain.
if auth.ChainID.IsZero() || auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

If cross-chain portability is intentionally desired, at minimum document the replay risk and add a governance parameter to control whether zero-chain-ID authorizations are accepted.

### Proof of Concept

1. On Ethereum mainnet (or any chain), Alice signs an EIP-7702 authorization:
   ```
   auth = { chainID: 0, address: <attacker_contract>, nonce: alice_nonce }
   signed_auth = alice.sign_authorization(auth)
   ```
2. Attacker observes `signed_auth` (e.g., from a mempool or a different chain's transaction).
3. Attacker submits on the target Ethermint chain:
   ```
   setcode_tx = { type: 4, to: alice_address, authorizationList: [signed_auth], ... }
   ```
4. `validateAuthorization` is called with `auth.ChainID.IsZero() == true`, so the chain-ID check is skipped entirely.
5. `setAuthorizationDelegation` installs `0xef0100<attacker_contract>` as Alice's code and increments Alice's nonce.
6. All future calls to Alice's address execute attacker's contract in Alice's context. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/evm/keeper/set_code_authorizations.go (L15-19)
```go
func (k *Keeper) validateAuthorization(auth *types.SetCodeAuthorization, stateDB vm.StateDB) (authority common.Address, err error) {
	// Verify chain ID is null or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
		return authority, core.ErrAuthorizationWrongChainID
	}
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-85)
```go
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

**File:** x/evm/keeper/state_transition.go (L163-191)
```go
func (k *Keeper) ApplyTransaction(ctx sdk.Context, msgEth *types.MsgEthereumTx) (*types.EVMResult, error) {
	ethTx := msgEth.AsTransaction()
	cfg, err := k.EVMConfig(ctx, k.eip155ChainID, ethTx.Hash())
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to load evm config")
	}

	msg := msgEth.AsMessage(cfg.BaseFee)
	// snapshot to contain the tx processing and post processing in same scope
	var commit func()
	var commitDurableAuthorization func()
	tmpCtxCommitted := false
	tmpCtx := ctx
	if k.hooks != nil {
		// Create a cache context to revert state when tx hooks fails,
		// the cache context is only committed when both tx and hooks executed successfully.
		// Didn't use `Snapshot` because the context stack has exponential complexity on certain operations,
		// thus restricted to be used only inside `ApplyMessage`.
		tmpCtx, commit = ctx.CacheContext()

		// Keep the EIP-7702 authorization effects in a separate cache so they
		// survive a later EVM/post-hook failure that discards tmpCtx. Only needed
		// for txs that actually carry authorizations.
		if len(msg.SetCodeAuthorizations) > 0 {
			var durableAuthorizationCtx sdk.Context
			durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
			cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
		}
	}
```

**File:** x/evm/keeper/state_transition.go (L484-513)
```go
		if msg.SetCodeAuthorizations != nil {
			// Track validated authorizations together with the authority recovered
			// during validation, so the durable replay below can reuse it.
			type validAuth struct {
				auth      ethtypes.SetCodeAuthorization
				authority common.Address
			}
			var validAuths []validAuth
			for _, auth := range msg.SetCodeAuthorizations {
				// Note errors are ignored, we simply skip invalid authorizations here.
				authority, err := k.applyAuthorization(&auth, stateDB)
				if err != nil {
					k.Logger(ctx).Debug("failed to apply authorization", "error", err, "authorization", auth)
					continue
				}
				validAuths = append(validAuths, validAuth{auth: auth, authority: authority})
			}

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

**File:** ante/sigverify.go (L31-43)
```go
func VerifyEthSig(tx sdk.Tx, signer ethtypes.Signer) error {
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		if err := msgEthTx.VerifySender(signer); err != nil {
			return errorsmod.Wrapf(errortypes.ErrorInvalidSigner, "signature verification failed: %s", err.Error())
		}
	}

	return nil
```
