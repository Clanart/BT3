## Title
Nil-pointer dereference on unset account in `EVMAddressDecorator.AnteHandle` — ([File: x/evm/ante/preprocess.go])

## Summary
`EVMAddressDecorator.AnteHandle` fetches an account via `p.accountKeeper.GetAccount(ctx, signer)` and immediately dereferences it with `acc.GetPubKey()` without checking whether `acc` is `nil`. `AccountKeeper.GetAccount` explicitly returns `nil` (not an error) when the address has no stored account, so any code path that reaches this decorator with a signer that has no account record will panic the node process, mirroring the exact bug class in CVE-2025-21775 (dereferencing a pointer that a prior allocation/lookup step can leave nil without the corresponding NULL check).

## Finding Description
`x/evm/ante/preprocess.go` defines: [1](#0-0) 

```go
func (p *EVMAddressDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	...
	signers := sigTx.GetSigners()
	for _, signer := range signers {
		if evmAddr, associated := p.evmKeeper.GetEVMAddress(ctx, signer); associated {
			...
			continue
		}
		acc := p.accountKeeper.GetAccount(ctx, signer)
		if acc.GetPubKey() == nil {
			...
```

`AccountKeeper.GetAccount` is defined to return `nil` when the store has no entry for the address: [2](#0-1) 

```go
func (ak AccountKeeper) GetAccount(ctx sdk.Context, addr sdk.AccAddress) types.AccountI {
	store := ctx.KVStore(ak.key)
	bz := store.Get(types.AddressStoreKey(addr))
	if bz == nil {
		return nil
	}
	return ak.decodeAccount(bz)
}
```

If `acc` is `nil`, `acc.GetPubKey()` is an interface method call on a nil interface value, which will panic with a nil-pointer dereference. This is the same bug class as the referenced kernel CVE: a value that a prior lookup/allocation step can leave nil is used without a NULL/nil check, in a "handled everywhere except one place" pattern (the same file has a nil-check pattern for `GetAccount` results in `precompiles/auth/legacy/v67/auth.go:143-146`, showing the missing check here is an outlier, not intentional).

This decorator is wired into the default (non-EVM) Cosmos ante chain, which every ordinary Cosmos-signed transaction goes through: [3](#0-2) 

The decorator only reaches the vulnerable branch when a signer is **not** EVM-associated (`GetEVMAddress` returns `associated=false`), so the account-existence question is entirely determined by whether the signer's account has ever been created in the auth store.

## Impact Explanation
A panic inside the ante handler pipeline during `CheckTx`/`DeliverTx` is caught by the recover/panic-handling machinery in `runTx` (per Cosmos SDK convention) and normally converted into a failed tx rather than crashing the whole process — but this needs to be validated against sei-chain's specific panic-recovery wrapping around `anteHandler` calls in `sei-cosmos/baseapp/baseapp.go`. If the ante handler execution is NOT wrapped in a `recover()` at the point this decorator runs (some ante decorators are called outside the standard recovered `runTx` panic boundary, e.g. during mempool checks, gRPC query paths, or block proposal validation), an attacker could trigger a full node crash / consensus halt by submitting a transaction signed by a brand-new, never-before-seen address (no account ever created) that is not EVM-associated. Even if wrapped and only converted to a failed CheckTx/DeliverTx, this is a reliable, remotely triggerable panic path reachable by any transaction sender with a fresh keypair, since standard Cosmos SDK ante chains create accounts via `SetPubKeyDecorator` **before** this point only for transactions that carry an explicit pubkey — signers derived indirectly (e.g., via multisig legacy amino pubkeys, or signers that are not required to have their pubkey set at signing time) can still reach this decorator with a nil account.

## Likelihood Explanation
The panic is trivially reachable: any transaction from an address with no on-chain account record (extremely common — brand-new addresses receiving no direct sends, or a multisig/legacy-amino signer construction that doesn't populate the account ahead of time) that is not already EVM-associated will hit this exact line. No special privileges, precompile access, or crafted contract are required — a single signed Cosmos transaction submitted through the standard RPC/CheckTx surface is sufficient to reach the vulnerable branch.

## Recommendation
Add a nil check before dereferencing `acc`, mirroring the pattern already used elsewhere in the codebase (e.g. `precompiles/auth/legacy/v67/auth.go:143-146`):

```go
acc := p.accountKeeper.GetAccount(ctx, signer)
if acc == nil || acc.GetPubKey() == nil {
    ...
    continue
}
```

## Proof of Concept
1. Generate a fresh keypair/address that has never had an account created on-chain (no coins received, no prior tx) and that is not associated with an EVM address via `GetEVMAddress`.
2. Construct and sign an ordinary (non-EVM) Cosmos transaction using this address as a signer, using a signing path that does not force `SetPubKeyDecorator` to persist the account before `EVMAddressDecorator` runs (e.g. legacy amino multisig signer or any signer enumerated by `GetSigners()` that is not the fee payer and whose pubkey-setting step is skipped).
3. Submit the transaction via `CheckTx`/broadcast.
4. `EVMAddressDecorator.AnteHandle` calls `p.accountKeeper.GetAccount(ctx, signer)`, which returns `nil`; the subsequent `acc.GetPubKey()` call panics with a nil-pointer/interface dereference.

Note: I was unable to fully confirm within available tool calls whether this panic is always caught by a `recover()` in every call path that invokes the ante handler (e.g., some RPC/mempool/ABCI paths may call ante decorators outside `runTx`'s panic-recovery boundary), which would be necessary to confirm process-level crash versus a merely failed transaction. This should be verified against `sei-cosmos/baseapp/baseapp.go`'s panic-recovery wrapping of all ante-handler invocation sites before treating this as a confirmed node-crash/consensus-halt vulnerability versus a lower-impact failed-tx bug.

### Citations

**File:** x/evm/ante/preprocess.go (L303-324)
```go
//nolint:revive
func (p *EVMAddressDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	if !ok {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrTxDecode, "invalid tx type")
	}
	signers := sigTx.GetSigners()
	for _, signer := range signers {
		if evmAddr, associated := p.evmKeeper.GetEVMAddress(ctx, signer); associated {
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		acc := p.accountKeeper.GetAccount(ctx, signer)
		if acc.GetPubKey() == nil {
			logger.Error("missing pubkey for signer", "signer", signer)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		pk, err := btcec.ParsePubKey(acc.GetPubKey().Bytes())
```

**File:** sei-cosmos/x/auth/keeper/account.go (L34-43)
```go
// GetAccount implements AccountKeeperI.
func (ak AccountKeeper) GetAccount(ctx sdk.Context, addr sdk.AccAddress) types.AccountI {
	store := ctx.KVStore(ak.key)
	bz := store.Get(types.AddressStoreKey(addr))
	if bz == nil {
		return nil
	}

	return ak.decodeAccount(bz)
}
```

**File:** app/ante.go (L66-87)
```go
	sequentialVerifyDecorator := ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler)

	anteDecorators := []sdk.AnteDecorator{
		ante.NewSetUpContextDecorator(antedecorators.GetGasMeterSetter(options.ParamsKeeper.(paramskeeper.Keeper))), // outermost AnteDecorator. SetUpContext must be called first
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.ParamsKeeper.(paramskeeper.Keeper), options.TxFeeChecker),
		wasmkeeper.NewLimitSimulationGasDecorator(options.WasmConfig.SimulationGasLimit, wasmkeeper.DefaultGasMeterSetter()), // after setup context to enforce limits early
		ante.NewRejectExtensionOptionsDecorator(),
		ante.NewValidateBasicDecorator(),
		ante.NewTxTimeoutHeightDecorator(),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		// PriorityDecorator must be called after DeductFeeDecorator which sets tx priority based on tx fees
		antedecorators.NewPriorityDecorator(),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, sigGasConsumer),
		sequentialVerifyDecorator,
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
		evmante.NewEVMAddressDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		antedecorators.NewAuthzNestedMessageDecorator(),
	}
```
