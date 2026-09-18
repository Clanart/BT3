### Title
`RegisterPointerDisabled` governance kill-switch is not enforced on the EVM `pointer` precompile's `AddNative`/`AddCW20`/`AddCW721`/`AddCW1155` methods - ([File: precompiles/pointer/pointer.go])

### Summary
The Sei chain has a governance-controlled param, `RegisterPointerDisabled`, whose stated purpose is to disable creation of new CW↔ERC pointer contracts. It is correctly enforced in the Cosmos `MsgRegisterPointer` handler (`x/evm/keeper/msg_server.go`), but the equivalent EVM-side entry point — the `pointer` precompile at `0x...100b` (`precompiles/pointer/pointer.go`), which creates the reverse-direction ERC pointer contracts for native/CW20/CW721/CW1155 assets — never checks this flag at all. This is structurally the same class of bug as `DelegatorFactory::create` failing to check the `blacklisted[type_]` mapping before instantiating a new entity: one gate (`blacklist`/`RegisterPointerDisabled`) exists and is enforced on one code path, but a second, equally reachable code path that performs the same privileged action (deploying a new pointer/entity) skips the check entirely.

### Finding Description
`x/evm/keeper/msg_server.go`'s `RegisterPointer` handler enforces the kill switch before creating any CW pointer: [1](#0-0) 

The corresponding param getter is: [2](#0-1) 

and a dedicated upgrade migration exists specifically to flip this flag on for governance to permanently disable pointer registration: [3](#0-2) 

However, the `pointer` EVM precompile — reachable by any EVM transaction sender calling `addNativePointer`, `addCW20Pointer`, `addCW721Pointer`, or `addCW1155Pointer` at address `0x000000000000000000000000000000000000100b` — creates new pointer contracts via `UpsertERCNativePointer` / `UpsertERCCW20Pointer` / `UpsertERCCW721Pointer` / `UpsertERCCW1155Pointer` without ever calling `GetRegisterPointerDisabled`: [4](#0-3) [5](#0-4) 

This mirrors the legacy versions of the same precompile (`v552`, `v555`, `v562`, `v610`), none of which check `RegisterPointerDisabled` either: [6](#0-5) 

The intent of `RegisterPointerDisabled` (as evidenced by its dedicated migration `MigrateDisableRegisterPointer` that force-enables it for all chains) is clearly to be a blanket switch preventing further pointer creation activity, analogous to the blacklist mapping in `DelegatorFactory`. Because the EVM-side creation path is a completely separate code path from the Cosmos `MsgRegisterPointer` handler, flipping `RegisterPointerDisabled` to `true` does not actually stop new pointer contracts from being created — an attacker or any ordinary user can still call the `pointer` precompile directly from an EVM tx to instantiate new ERC-native/CW20/CW721/CW1155 pointer contracts, exactly like `DelegatorFactory::create` still permitting creation for a type marked in the `blacklisted` mapping.

### Impact Explanation
This breaks the governance/operational control the `RegisterPointerDisabled` param was introduced to provide (see the dedicated upgrade migration whose only job is to set it to `true`). If pointer creation is disabled because of a known issue with the pointer-deployment mechanism (bytecode bug, address-collision issue, deterministic address griefing, etc.), the EVM `pointer` precompile remains fully open and can still be used to deploy new pointer contracts, defeating the purpose of the kill switch. Since pointer contracts hold the trust boundary for CW20/CW721/CW1155/native-usei bridging into the EVM (bank/tokenfactory <-> EVM ERC bridge), continuing to allow the creation of unvetted pointer instances after the mechanism was intentionally disabled is a real supply/consistency risk (e.g., pointer-to-pointer or double-registration edge cases the disable was meant to block), warranting Medium severity.

### Likelihood Explanation
High reachability: any unprivileged EVM account can call the `pointer` precompile's `addNativePointer`/`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` methods directly in a normal EVM transaction — no special permission, no delegatecall context needed (only disallowed via `readOnly`/delegatecall guard). The bypass is deterministic and requires no race condition; it only requires that governance has set `RegisterPointerDisabled = true` (or the migration has run), while the caller still invokes the EVM precompile methods that were never gated.

### Recommendation
Add the same `RegisterPointerDisabled` check performed in `x/evm/keeper/msg_server.go`'s `RegisterPointer` to the `pointer` precompile's `Execute`/`AddNative`/`AddCW20`/`AddCW721`/`AddCW1155` methods in `precompiles/pointer/pointer.go` (and any still-served legacy versions), e.g.:
```go
func (p PrecompileExecutor) Execute(...) (...) {
    if p.evmKeeper.GetRegisterPointerDisabled(ctx) {
        return nil, 0, errors.New("registering CW->ERC pointers has been disabled")
    }
    ...
}
```
This centralizes the check once at `Execute` so all four pointer-creation methods are covered consistently with the Cosmos-side handler.

### Proof of Concept
1. Governance runs the `MigrateDisableRegisterPointer` upgrade (or otherwise sets `params.RegisterPointerDisabled = true`), intending to stop all new pointer contract creation.
2. Any user submits a normal EVM transaction calling `addCW20Pointer(cwAddress)` (or `addNativePointer`/`addCW721Pointer`/`addCW1155Pointer`) on the `pointer` precompile at `0x000000000000000000000000000000000000100b`.
3. `PrecompileExecutor.AddCW20` in `precompiles/pointer/pointer.go` proceeds without consulting `GetRegisterPointerDisabled`, successfully calls `UpsertERCCW20Pointer`, and deploys/upserts a new ERC pointer contract for the CW20 token.
4. Meanwhile, `seid tx evm register-cw-pointer` (which goes through `MsgRegisterPointer` in `x/evm/keeper/msg_server.go`) correctly fails with "registering CW->ERC pointers has been disabled" for the reverse direction, demonstrating the inconsistency: the disable flag is honored on one path and silently ignored on the other.

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

**File:** precompiles/pointer/pointer.go (L134-164)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L90-109)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if caller.Cmp(callingContract) != 0 {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, suppliedGas, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, suppliedGas, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, suppliedGas, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```
