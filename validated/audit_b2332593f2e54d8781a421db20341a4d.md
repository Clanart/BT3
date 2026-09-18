Confirmed: the `RegisterPointerDisabled` param check is only enforced in `x/evm/keeper/msg_server.go`'s `RegisterPointer` handler, and is completely absent from the `pointer` EVM precompile (`precompiles/pointer/pointer.go`), which exposes equivalent `addNativePointer`, `addCW20Pointer`, `addCW721Pointer`, and `addCW1155Pointer` methods that call the same underlying `UpsertERC*Pointer` keeper functions.

### Title
Missing `RegisterPointerDisabled` check in the pointer precompile lets EVM callers bypass the governance-controlled pointer-registration pause - (File: `precompiles/pointer/pointer.go`)

### Summary
The `x/evm` module gates CW→ERC pointer registration behind a governance-settable param, `RegisterPointerDisabled`, checked in the Cosmos-message path `RegisterPointer`. The EVM precompile that exposes the same functionality (`AddNativePointer`, `AddCW20Pointer`, `AddCW721Pointer`, `AddCW1155Pointer`) never checks this param, so any EVM transaction sender can bypass the pause.

### Finding Description
`RegisterPointer` in `x/evm/keeper/msg_server.go` explicitly returns an error when the module has been paused via `RegisterPointerDisabled`: [1](#0-0) 

This param is a governance/operator kill-switch — it is set via a migration (`MigrateDisableRegisterPointer`) and is the documented mechanism to halt further pointer creation, e.g. during an upgrade or when the pointer contract logic needs to be frozen: [2](#0-1) 

However, the `pointer` precompile at address `0x000000000000000000000000000000000000100b`, which any EVM contract or EOA transaction can invoke, implements the exact same pointer-creation operations (`AddNative`, `AddCW20`, `AddCW721`, `AddCW1155`) by calling directly into `p.evmKeeper.UpsertERCNativePointer` / `UpsertERCCW20Pointer` / `UpsertERCCW721Pointer` / `UpsertERCCW1155Pointer`, with no reference to `RegisterPointerDisabled` anywhere in the file: [3](#0-2) [4](#0-3) 

The `Execute` entry point only checks for `readOnly`/`staticcall` and delegatecall restrictions, not the module-level pause flag: [5](#0-4) 

This is a structurally identical bug to the reported flatmoney issue: one entry point into a shared piece of business logic enforces a pause/guard, and a second, equally reachable entry point into the *same* underlying state-mutating function omits it, letting unprivileged callers bypass the intended halt.

### Impact Explanation
`RegisterPointerDisabled` exists specifically so that pointer creation/migration logic can be frozen (e.g. mid-upgrade, or if a bug in pointer bytecode/versioning is discovered) without needing a full chain halt. Because the precompile path is unguarded, any EVM caller can continue creating or upgrading CW↔EVM pointers via `addNativePointer`/`addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` while the chain operators believe registration is paused. Since pointer registration deploys bytecode and wires new token/NFT pointer contracts that other EVM/CW users will interact with and transfer value through, creating pointers during a period intended to be frozen (e.g. while pointer contract templates are being upgraded, per `x/evm/keeper/pointer_upgrade.go`) can produce pointers built against stale/incompatible logic, or race with an in-flight upgrade in ways the pause was meant to prevent — risking inconsistent pointer state that downstream transfers rely on.

### Likelihood Explanation
High likelihood of triggerability: `RegisterPointerDisabled` is a real, exercised protocol control (with dedicated migration and tests), the precompile is public and requires no special permissions to call, and the omission is unconditional — every call to the precompile's pointer-adding methods skips the check regardless of chain state.

### Proof of Concept
1. Governance/operator sets `RegisterPointerDisabled = true` (as done by `MigrateDisableRegisterPointer` in `x/evm/migrations/disable_register_pointer.go`), intending to halt all new CW↔ERC pointer creation.
2. Confirm the Cosmos-message path is blocked: submitting `MsgRegisterPointer` now fails with `"registering CW->ERC pointers has been disabled"`, as covered by `TestRegisterPointerDisabled` in `x/evm/keeper/msg_server_test.go` (lines 836-876).
3. From any EVM account, call the `pointer` precompile at `0x000000000000000000000000000000000000100b` with `addCW20Pointer(cwAddress)` (or `addNativePointer`, `addCW721Pointer`, `addCW1155Pointer`).
4. `PrecompileExecutor.Execute` routes to `AddCW20`/`AddNative`/etc., which call `k.UpsertERCCW20Pointer`/`UpsertERCNativePointer` directly — no check of `RegisterPointerDisabled` exists anywhere in `precompiles/pointer/pointer.go` — and the pointer is created successfully despite the pause flag being set. [1](#0-0) [6](#0-5)

### Citations

**File:** x/evm/keeper/msg_server.go (L247-251)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
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

**File:** precompiles/pointer/pointer.go (L1-228)
```go
package pointer

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"

	ethabi "github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/tracing"
	"github.com/ethereum/go-ethereum/core/vm"
	pcommon "github.com/sei-protocol/sei-chain/precompiles/common"
	putils "github.com/sei-protocol/sei-chain/precompiles/utils"
	sdk "github.com/sei-protocol/sei-chain/sei-cosmos/types"
	"github.com/sei-protocol/sei-chain/utils"
)

const (
	PrecompileName   = "pointer"
	AddNativePointer = "addNativePointer"
	AddCW20Pointer   = "addCW20Pointer"
	AddCW721Pointer  = "addCW721Pointer"
	AddCW1155Pointer = "addCW1155Pointer"
)

const PointerAddress = "0x000000000000000000000000000000000000100b"

// Embed abi json file to the executable binary. Needed when importing as dependency.
//
//go:embed abi.json
var f embed.FS

type PrecompileExecutor struct {
	evmKeeper   putils.EVMKeeper
	bankKeeper  putils.BankKeeper
	wasmdKeeper putils.WasmdViewKeeper

	AddNativePointerID []byte
	AddCW20PointerID   []byte
	AddCW721PointerID  []byte
	AddCW1155PointerID []byte
}

func NewPrecompile(keepers putils.Keepers) (*pcommon.DynamicGasPrecompile, error) {
	newAbi := pcommon.MustGetABI(f, "abi.json")

	p := &PrecompileExecutor{
		evmKeeper:   keepers.EVMK(),
		bankKeeper:  keepers.BankK(),
		wasmdKeeper: keepers.WasmdVK(),
	}

	for name, m := range newAbi.Methods {
		switch name {
		case AddNativePointer:
			p.AddNativePointerID = m.ID
		case AddCW20Pointer:
			p.AddCW20PointerID = m.ID
		case AddCW721Pointer:
			p.AddCW721PointerID = m.ID
		case AddCW1155Pointer:
			p.AddCW1155PointerID = m.ID
		}
	}

	return pcommon.NewDynamicGasPrecompile(newAbi, p, common.HexToAddress(PointerAddress), PrecompileName), nil
}

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

func (p PrecompileExecutor) EVMKeeper() putils.EVMKeeper {
	return p.evmKeeper
}

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

func (p PrecompileExecutor) AddCW721(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}

func (p PrecompileExecutor) AddCW1155(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
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
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```
