### Title
`RegisterPointerDisabled` param gate is bypassable via the EVM `pointer` precompile - ([File: precompiles/pointer/pointer.go])

### Summary
The Cosmos `MsgRegisterPointer` handler enforces the `RegisterPointerDisabled` chain param, but the equivalent operation reachable through the EVM `pointer` precompile (`0x...100b`) never checks it. Once governance/upgrade sets `RegisterPointerDisabled=true` to stop new CW→ERC pointer creation, any EVM caller can still create pointers by calling the precompile directly, exactly mirroring the Keycloak advisory pattern where one gate (`checkAccountApiEnabled()`) was applied to some endpoints of a feature but missing from others performing the same class of operation.

### Finding Description
`x/evm/keeper/msg_server.go`'s `RegisterPointer` handler checks the disable flag before creating/upgrading a CW20/CW721/CW1155 pointer contract: [1](#0-0) 

The flag is read via `Keeper.GetRegisterPointerDisabled`, backed by the `Params.RegisterPointerDisabled` param: [2](#0-1) 

A dedicated migration exists specifically to flip this flag to `true` on-chain (`MigrateDisableRegisterPointer`), confirming this is meant to be a chain-wide kill switch for pointer creation: [3](#0-2) 

However, the same pointer-creation functionality is also exposed through the EVM `pointer` precompile (`precompiles/pointer/pointer.go`), whose `Execute`/`AddNative`/`AddCW20`/`AddCW721`/`AddCW1155` methods perform the identical `UpsertERCNativePointer` / `UpsertERCCW20Pointer` / `UpsertERCCW721Pointer` / `UpsertERCCW1155Pointer` keeper calls — with no call to `GetRegisterPointerDisabled` anywhere in the file: [4](#0-3) [5](#0-4) 

A grep across the codebase confirms `GetRegisterPointerDisabled`/`SetRegisterPointerDisabled` are referenced only in `msg_server.go`, `params.go`, and test files — never inside `precompiles/pointer/pointer.go` or any of its legacy versions (`precompiles/pointer/legacy/v552`…`v640`), all of which contain the same unguarded `AddNative`/`AddCW20`/`AddCW721`/`AddCW1155(1155)` code paths.

### Impact Explanation
This is the direct sei-chain analog of the Keycloak forced-browsing bug: a security/feature control (the pointer-creation kill switch) is enforced on one entry point (Cosmos `MsgRegisterPointer`) but not on a second, fully-functional entry point (the EVM precompile) performing the same privileged state-mutating operation. If operators disable pointer registration via governance/upgrade — presumably because of a known issue with pointer creation (a dedicated migration was written to force this off) — any EVM contract or externally-owned account can still call `0x...100b` directly to register/upgrade CW20/CW721/CW1155 pointers, completely defeating the intended chain-wide restriction. Because pointer contracts mediate CW↔EVM token bridging, unauthorized/unintended pointer creation while the feature is supposed to be disabled can enable unauthorized token bridging behavior the operators specifically intended to block, undermining the integrity control the flag exists to provide.

### Likelihood Explanation
High likelihood of exploitation once the flag is set to `true`: no special privileges are needed — any address that can send an EVM transaction or make a contract call can invoke the precompile's `addNativePointer`/`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` methods, which pass all of `readOnly`/delegatecall checks and proceed straight to pointer creation with zero reference to the disable param.

### Recommendation
Add a check for `evmKeeper.GetRegisterPointerDisabled(ctx)` at the top of `PrecompileExecutor.Execute` in `precompiles/pointer/pointer.go` (returning an error such as `"registering CW->ERC pointers has been disabled"`, matching the message used in `msg_server.go`) before dispatching to `AddNative`/`AddCW20`/`AddCW721`/`AddCW1155`. Apply the same fix to any currently-live legacy precompile versions still reachable at runtime.

### Proof of Concept
1. Governance/upgrade sets `Params.RegisterPointerDisabled = true` (as done by `MigrateDisableRegisterPointer`).
2. Confirm `MsgRegisterPointer` now fails: `server.GetRegisterPointerDisabled(ctx)` returns `true`, so the Cosmos tx path returns `"registering CW->ERC pointers has been disabled"`.
3. From any EVM account, call the `pointer` precompile at `0x000000000000000000000000000000000000100b` with `addCW20Pointer(cwAddr)` (or `addNativePointer`/`addCW721Pointer`/`addCW1155Pointer`).
4. `PrecompileExecutor.Execute` → `AddCW20` runs unconditionally, queries the CW20 contract, and calls `evmKeeper.UpsertERCCW20Pointer`, successfully creating the pointer contract and returning its address — despite the chain-wide disable flag being set.

### Citations

**File:** x/evm/keeper/msg_server.go (L247-251)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
	}
```

**File:** x/evm/keeper/params.go (L212-223)
```go
func (k *Keeper) GetRegisterPointerDisabled(ctx sdk.Context) bool {
	if !ctx.IsTracing() {
		return k.GetParams(ctx).RegisterPointerDisabled
	}
	switch {
	case semver.Compare(ctx.ClosestUpgradeName(), "v6.0.6") < 0:
		// Not present in pre-5.8.0 params; use default
		return types.DefaultRegisterPointerDisabled
	default:
		return k.GetParams(ctx).RegisterPointerDisabled
	}
}
```

**File:** x/evm/migrations/disable_register_pointer.go (L1-13)
```go
package migrations

import (
	sdk "github.com/sei-protocol/sei-chain/sei-cosmos/types"
	"github.com/sei-protocol/sei-chain/x/evm/keeper"
)

func MigrateDisableRegisterPointer(ctx sdk.Context, k *keeper.Keeper) error {
	params := k.GetParams(ctx)
	params.RegisterPointerDisabled = true
	k.SetParams(ctx, params)
	return nil
}
```

**File:** precompiles/pointer/pointer.go (L72-93)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```

**File:** precompiles/pointer/pointer.go (L99-132)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```
