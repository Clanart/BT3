### Title
Non-Deterministic EIP-2935 Preinstall Bytecode via Non-Constant `params.HistoryStorageCode` Causes Consensus Failure - (File: `x/evm/types/preinstall.go`)

### Summary

`DefaultPreinstalls` in `x/evm/types/preinstall.go` derives the EIP-2935 history-storage contract bytecode from `params.HistoryStorageCode`, a **non-constant variable** imported from go-ethereum. Every other preinstall entry uses a hardcoded hex literal. If go-ethereum is upgraded and `params.HistoryStorageCode` changes, validators running different binary versions will deploy different bytecode at `params.HistoryStorageAddress` during `InitGenesis`, producing divergent state roots and a deterministic consensus failure.

### Finding Description

`DefaultPreinstalls` is declared as a `var` (not `const`) and is initialized at package load time:

```go
// x/evm/types/preinstall.go
var DefaultPreinstalls = []Preinstall{
    { Name: "Create2",  Address: "0x4e59b44847...", Code: "0x7fff..." },   // hardcoded
    { Name: "Multicall3", ...                       Code: "0x6080..." },   // hardcoded
    { Name: "Permit2",  ...                         Code: "0x6040..." },   // hardcoded
    { Name: "Safe singleton factory", ...           Code: "0x7fff..." },   // hardcoded
    {
        Name:    "EIP-2935 - Serve historical block hashes from state",
        Address: params.HistoryStorageAddress.String(),
        Code:    common.Bytes2Hex(params.HistoryStorageCode),  // ← NOT hardcoded
    },
}
``` [1](#0-0) 

The four other entries embed their bytecode as compile-time string literals. The EIP-2935 entry reads `params.HistoryStorageCode` at runtime from go-ethereum's `params` package. That symbol is a `var []byte` in go-ethereum, not a `const`, and its value has already changed across go-ethereum releases (e.g., EIP-2935 was revised between go-ethereum v1.14.x and v1.15.x).

`NewEVMGenesisState()` in `evmd/genesis.go` assigns `DefaultPreinstalls` directly into the genesis state:

```go
evmGenState.Preinstalls = evmtypes.DefaultPreinstalls
``` [2](#0-1) 

`InitGenesis` then calls `k.AddPreinstalls(ctx, data.Preinstalls)`, which writes the bytecode and its keccak256 hash into the KV store and the account keeper:

```go
k.SetCode(ctx, codeHashBytes, common.FromHex(preinstall.Code))
``` [3](#0-2) 

`InitGenesis` is invoked during `InitChain`, which is part of the CometBFT ABCI consensus flow. Every validator independently executes it. If two validators link against different go-ethereum versions where `params.HistoryStorageCode` differs, they store different bytes and different code hashes at `params.HistoryStorageAddress`, producing different app-hash values after `InitChain` and immediately halting consensus.

Additionally, `ExportGenesis` does **not** re-export the `Preinstalls` field:

```go
return &types.GenesisState{
    Accounts: ethGenAccounts,
    Params:   k.GetParams(ctx),
    // Preinstalls omitted
}
``` [4](#0-3) 

This means a chain-export/re-import cycle (the standard Cosmos upgrade path) re-runs `AddPreinstalls` with an empty list, so the preinstall accounts survive only as regular `GenesisAccount` entries. Any future migration that re-invokes `AddPreinstalls(ctx, DefaultPreinstalls)` would again read the live `params.HistoryStorageCode` value, reintroducing the non-determinism.

### Impact Explanation

Validators running different go-ethereum versions compute different keccak256 hashes for the EIP-2935 contract, write different bytes to the EVM code store, and therefore produce different IAVL state roots after `InitChain`. CometBFT requires all validators to agree on the app-hash after `InitChain`; a mismatch causes an immediate, unrecoverable consensus halt. This matches the **Critical** allowed impact: "block-processing path can halt the chain or cause deterministic validator consensus failure."

### Likelihood Explanation

Go-ethereum is a fast-moving dependency. `params.HistoryStorageCode` changed when EIP-2935 was finalized. Any Ethermint upgrade that bumps the go-ethereum dependency version without simultaneously pinning the EIP-2935 bytecode as a constant will silently change the value of `DefaultPreinstalls`. Validators that upgrade at different times (a common pattern in Cosmos validator sets) will diverge. No privileged access or special transaction is required; the divergence is triggered purely by the normal `InitChain` ABCI call.

### Recommendation

Replace the dynamic reference with a hardcoded constant hex string, exactly as the other four preinstalls do:

```go
// x/evm/types/preinstall.go
{
    Name:    "EIP-2935 - Serve historical block hashes from state",
    Address: "0x0000F90827F1C53a10cb7A02335B175320002935",
    Code:    "0x<exact_bytes_pinned_at_audit_time>",
},
```

Additionally, `ExportGenesis` should export the `Preinstalls` field so that the deployed bytecode is preserved verbatim across chain-export/re-import cycles, rather than being re-derived from the live go-ethereum variable.

### Proof of Concept

1. Build two Ethermint binaries: one linking go-ethereum vX (where `params.HistoryStorageCode` = `bytesA`) and one linking go-ethereum vY (where `params.HistoryStorageCode` = `bytesB`, `bytesA ≠ bytesB`).
2. On each binary, call `evmtypes.DefaultPreinstalls` and observe the `Code` field of the EIP-2935 entry differs.
3. Start a two-validator testnet where validator-1 uses binary vX and validator-2 uses binary vY.
4. Both validators execute `InitChain` → `InitGenesis` → `AddPreinstalls`.
5. Validator-1 stores `bytesA` at `HistoryStorageAddress`; validator-2 stores `bytesB`.
6. The resulting app-hashes differ; CometBFT logs `"wrong Block.Header.AppHash"` and the chain halts at height 1 without any transaction ever being submitted. [5](#0-4) [6](#0-5)

### Citations

**File:** x/evm/types/preinstall.go (L13-38)
```go
var DefaultPreinstalls = []Preinstall{
	{
		Name:    "Create2",
		Address: "0x4e59b44847b379578588920ca78fbf26c0b4956c",
		Code:    "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe03601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3",
	},
	{
		Name:    "Multicall3",
		Address: "0xcA11bde05977b3631167028862bE2a173976CA11",
		Code:    "0x6080604052600436106100f35760003560e01c80634d2301cc1161008a578063a8b0574e11610059578063a8b0574e1461025a578063bce38bd714610275578063c3077fa914610288578063ee82ac5e1461029b57600080fd5b80634d2301cc146101ec57806372425d9d1461022157806382ad56cb1461023457806386d516e81461024757600080fd5b80633408e470116100c65780633408e47014610191578063399542e9146101a45780633e64a696146101c657806342cbb15c146101d957600080fd5b80630f28c97d146100f8578063174dea711461011a578063252dba421461013a57806327e86d6e1461015b575b600080fd5b34801561010457600080fd5b50425b6040519081526020015b60405180910390f35b61012d610128366004610a85565b6102ba565b6040516101119190610bbe565b61014d610148366004610a85565b6104ef565b604051610111929190610bd8565b34801561016757600080fd5b50437fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff ... (truncated)
	},
	{
		Name:    "Permit2",
		Address: "0x000000000022D473030F116dDEE9F6B43aC78BA3",
		Code:    "0x6040608081526004908136101561001557600080fd5b600090813560e01c80630d58b1db1461126c578063137c29fe146110755780632a2d80d114610db75780632b67b57014610bde57806330f28b7a14610ade5780633644e51514610a9d57806336c7851614610a285780633ff9dcb1146109a85780634fe02b441461093f57806365d9723c146107ac57806387517c451461067a578063927da105146105c3578063cc53287f146104a3578063edd9444b1461033a5763fe8ec1a7146100c657600080fd5b346103365760c07ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc3601126103365767ffffffffffffffff833581811161033257610114903690860161164b565b60243582811161032e5761012b903690870161161a565b6101336114e6565b9160843585811161032a5761014b9036908a016115c1565b98909560a43590811161032657610164913691016115c1565b969095815190610173826113ff565b606b82527f5065726d697442617463685769746e65 ... (truncated)
	},
	{
		Name:    "Safe singleton factory",
		Address: "0x914d7Fec6aaC8cd542e72Bca78B30650d45643d7",
		Code:    "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe03601600081602082378035828234f58015156039578182fd5b8082525050506014600cf3",
	},
	{
		Name:    "EIP-2935 - Serve historical block hashes from state",
		Address: params.HistoryStorageAddress.String(),
		Code:    common.Bytes2Hex(params.HistoryStorageCode),
	},
```

**File:** evmd/genesis.go (L22-26)
```go
func NewEVMGenesisState() *evmtypes.GenesisState {
	evmGenState := evmtypes.DefaultGenesisState()
	evmGenState.Preinstalls = evmtypes.DefaultPreinstalls

	return evmGenState
```

**File:** x/evm/keeper/keeper.go (L463-463)
```go
			k.SetCode(ctx, codeHashBytes, common.FromHex(preinstall.Code))
```

**File:** x/evm/genesis.go (L85-87)
```go
	if err := k.AddPreinstalls(ctx, data.Preinstalls); err != nil {
		panic(fmt.Errorf("error adding preinstalls: %s", err))
	}
```

**File:** x/evm/genesis.go (L116-119)
```go
	return &types.GenesisState{
		Accounts: ethGenAccounts,
		Params:   k.GetParams(ctx),
	}
```
