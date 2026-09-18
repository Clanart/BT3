### Title
Missing codehash/whitelist check on CosmWasm→EVM delegatecall lets a pointer contract migration or unvetted CW code execute EVM calls under the caller's associated address - ([File: x/evm/keeper/evm.go])

### Summary
`MsgInternalEVMDelegateCall` carries a `CodeHash` field that is populated by the wasmd message encoder from the calling CW contract's code hash at message-construction time [1](#0-0) , and the `x/evm` params define a `WhitelistedCwCodeHashesForDelegateCall` allowlist intended to restrict which CW code hashes are permitted to trigger EVM delegatecalls [2](#0-1) . However, the actual handler for this message, `Keeper.HandleInternalEVMDelegateCall`, never reads or validates `req.CodeHash` against that whitelist (or against anything else), and `ValidateBasic()` on the message is a no-op [3](#0-2) .

### Finding Description
The EVM module's `AGENTS.md` documentation states delegate-calls from CosmWasm are "restricted to whitelisted pointer contracts" [4](#0-3) , and a `WhitelistedCwCodeHashesForDelegateCall` param exists precisely to pin which CW contract bytecode (by codehash) is authorized to make this privileged call [5](#0-4) . This mirrors the Timelock's intended codehash-pinning design: capture the codehash at "proposal"/message-construction time (`EncodeDelegateCallEVM` snapshots `codeInfo.CodeHash` from the CW contract's stored code) and verify it again at execution time so that code changes between the two points (e.g., contract migration) can't silently change behavior.

In sei-chain, that verification step is missing. `HandleInternalEVMDelegateCall` only checks:
1. That `req.To` is set,
2. That the reverse pointer registry maps `req.FromContract` to `req.To` (i.e., the caller is a registered pointer contract for that EVM target) [6](#0-5) ,
3. That the sender has an EVM address association [7](#0-6) .

It never compares `req.CodeHash` to `k.GetWhitelistedCwCodeHashesForDelegateCall(ctx)` / `Whitelist.IsHashInWhiteList` (which exists in `x/evm/types/whitelist.go` but is not called anywhere in `x/evm/keeper`) [8](#0-7) , nor does it re-fetch the current code hash of `FromContract` from the wasm keeper to confirm it still matches the hash captured when the message was built. A grep across `x/evm/keeper` confirms zero references to `WhitelistedCwCodeHashesForDelegateCall`, meaning the parameter and the `CodeHash` field on the message are effectively dead/unenforced.

Practically, the only real gate is `evmAddressIsPointer`/reverse-registry membership: any CW contract that is a registered CW20/CW721/CW1155 pointer for an EVM contract can delegatecall into that EVM contract with the sender's associated EVM address as `From`, regardless of what CW code hash it currently runs (including a hash outside any intended whitelist, or a hash that changed via `wasmKeeper.Migrate` after the whitelist/policy was set, since `RegisterPointer` explicitly allows migrating existing pointer contracts to a new code ID) [9](#0-8) .

### Impact Explanation
`CallEVM` executes with `from = senderEvmAddr` (the transaction submitter's associated EVM address) and `value = 0`, but arbitrary `data` supplied by the CW contract [10](#0-9) . Since only pointer-registry membership is checked and not the actual bytecode/codehash currently deployed at that pointer contract, if a pointer contract's CW code is migrated (via `MsgRegisterPointer`'s `Migrate` path) to logic that was never vetted against `WhitelistedCwCodeHashesForDelegateCall`, it can still make privileged delegatecalls into the EVM contract using any associated user's EVM identity that interacts with it. This could be leveraged to invoke unexpected EVM contract functions (e.g., pointer contract admin/transfer functions) as if they were the user, potentially leading to unauthorized asset moves through the pointer bridge.

### Likelihood Explanation
Reachable by any wasm module message sender who interacts with (or whose transaction routes through) a registered CW pointer contract; the delegate-call path is a documented, intentionally-exposed CW→EVM bridge feature (`EvmMsg::DelegateCallEvm`) usable from CosmWasm contracts [11](#0-10) . The precondition is that a pointer contract's CW code changes (migration) to something not intended to be delegatecall-eligible, which is governed by whoever controls pointer migration (module account via governance/params in current code), so likelihood depends on that migration path being reachable to an attacker or through a bug in migration authorization — this could not be fully confirmed within available context.

### Recommendation
In `Keeper.HandleInternalEVMDelegateCall` (x/evm/keeper/evm.go), before calling `CallEVM`, re-derive or fetch the current CW code hash of `req.FromContract` from the wasm keeper and validate it against `k.GetWhitelistedCwCodeHashesForDelegateCall(ctx)` using the existing `Whitelist.IsHashInWhiteList` helper, rather than trusting the client-supplied `req.CodeHash` or skipping the check entirely. Reject the call if the whitelist is non-empty and the current code hash isn't present.

### Proof of Concept
Not independently reproducible from static review alone: it requires confirming (1) that pointer contract migration is reachable without the same governance guard that gates the whitelist param, and (2) constructing a migrated pointer contract that issues a `DelegateCallEvm` message with attacker-controlled `data`. The code-level gap — `WhitelistedCwCodeHashesForDelegateCall` and `MsgInternalEVMDelegateCall.CodeHash` being defined but never read in `HandleInternalEVMDelegateCall` — is directly verifiable in the cited files.

### Citations

**File:** x/evm/client/wasm/encoder.go (L32-52)
```go
func EncodeDelegateCallEVM(rawMsg json.RawMessage, sender sdk.AccAddress, info wasmvmtypes.MessageInfo, codeInfo wasmtypes.CodeInfo) ([]sdk.Msg, error) {
	encodedCallEVM := bindings.DelegateCallEVM{}
	if err := json.Unmarshal(rawMsg, &encodedCallEVM); err != nil {
		return []sdk.Msg{}, err
	}
	decodedData, err := base64.StdEncoding.DecodeString(encodedCallEVM.Data)
	if err != nil {
		return []sdk.Msg{}, err
	}
	s := sender
	if origSender, err := sdk.AccAddressFromBech32(info.Sender); err == nil {
		s = origSender
	}
	internalCallEVMMsg := types.MsgInternalEVMDelegateCall{
		Sender:       s.String(),
		To:           encodedCallEVM.To,
		CodeHash:     codeInfo.CodeHash,
		Data:         decodedData,
		FromContract: sender.String(),
	}
	return []sdk.Msg{&internalCallEVMMsg}, nil
```

**File:** x/evm/types/params.go (L67-80)
```go
func (p *Params) ParamSetPairs() paramtypes.ParamSetPairs {
	return paramtypes.ParamSetPairs{
		paramtypes.NewParamSetPair(KeyPriorityNormalizer, &p.PriorityNormalizer, validatePriorityNormalizer),
		paramtypes.NewParamSetPair(KeyBaseFeePerGas, &p.BaseFeePerGas, validateBaseFeePerGas),
		paramtypes.NewParamSetPair(KeyMaxDynamicBaseFeeUpwardAdjustment, &p.MaxDynamicBaseFeeUpwardAdjustment, validateBaseFeeAdjustment),
		paramtypes.NewParamSetPair(KeyMaxDynamicBaseFeeDownwardAdjustment, &p.MaxDynamicBaseFeeDownwardAdjustment, validateBaseFeeAdjustment),
		paramtypes.NewParamSetPair(KeyMinFeePerGas, &p.MinimumFeePerGas, validateMinFeePerGas),
		paramtypes.NewParamSetPair(KeyWhitelistedCwCodeHashesForDelegateCall, &p.WhitelistedCwCodeHashesForDelegateCall, validateWhitelistedCwHashesForDelegateCall),
		paramtypes.NewParamSetPair(KeyDeliverTxHookWasmGasLimit, &p.DeliverTxHookWasmGasLimit, validateDeliverTxHookWasmGasLimit),
		paramtypes.NewParamSetPair(KeyTargetGasUsedPerBlock, &p.TargetGasUsedPerBlock, func(i interface{}) error { return nil }),
		paramtypes.NewParamSetPair(KeySeiSstoreSetGasEIP2200, &p.SeiSstoreSetGasEip2200, validateSeiSstoreSetGasEIP2200),
		paramtypes.NewParamSetPair(KeyMaxFeePerGas, &p.MaximumFeePerGas, validateMaxFeePerGas),
		paramtypes.NewParamSetPair(KeyRegisterPointerDisabled, &p.RegisterPointerDisabled, validateRegisterPointerDisabled),
	}
```

**File:** x/evm/types/params.go (L264-270)
```go
func validateWhitelistedCwHashesForDelegateCall(i interface{}) error {
	_, ok := i.([][]byte)
	if !ok {
		return fmt.Errorf("invalid parameter type: %T", i)
	}
	return nil
}
```

**File:** x/evm/types/message_internal_evm_delegate_call.go (L29-31)
```go
func (msg *MsgInternalEVMDelegateCall) ValidateBasic() error {
	return nil
}
```

**File:** x/evm/AGENTS.md (L165-171)
```markdown
## WASM Integration

CosmWasm contracts can interact with the EVM through two mechanisms:

- **Queries** — static EVM calls, ERC20/721/1155 token queries, address lookups.
- **Messages** — `MsgInternalEVMCall` for regular calls and `MsgInternalEVMDelegateCall` for delegate calls (restricted to whitelisted pointer contracts).

```

**File:** x/evm/keeper/evm.go (L47-58)
```go
func (k *Keeper) HandleInternalEVMDelegateCall(ctx sdk.Context, req *types.MsgInternalEVMDelegateCall) (*sdk.Result, error) {
	var to *common.Address
	if req.To != "" {
		addr := common.HexToAddress(req.To)
		to = &addr
	} else {
		return nil, errors.New("cannot use a CosmWasm contract to delegate-create an EVM contract")
	}
	addr, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(req.FromContract))))
	if !exists || common.BytesToAddress(addr).Cmp(*to) != 0 {
		return nil, errors.New("only pointer contract can make delegatecalls")
	}
```

**File:** x/evm/keeper/evm.go (L59-76)
```go
	zeroInt := sdk.ZeroInt()
	senderAddr, err := sdk.AccAddressFromBech32(req.Sender)
	if err != nil {
		return nil, err
	}
	// delegatecall caller must be associated; otherwise any state change on EVM contract will be lost
	// after they asssociate.
	senderEvmAddr, found := k.GetEVMAddress(ctx, senderAddr)
	if !found {
		err := types.NewAssociationMissingErr(req.Sender)
		evmKeeperMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "evm_handle_internal_evm_delegate_call"), attribute.String("type", err.AddressType())))
		return nil, err
	}
	ret, err := k.CallEVM(ctx, senderEvmAddr, to, &zeroInt, req.Data)
	if err != nil {
		return nil, err
	}
	return &sdk.Result{Data: ret}, nil
```

**File:** x/evm/types/whitelist.go (L1-14)
```go
package types

import "github.com/ethereum/go-ethereum/common"

func (w *Whitelist) IsHashInWhiteList(h common.Hash) bool {
	for _, s := range w.Hashes {
		if s == h.Hex() {
			return true
		}
	}
	return false
}


```

**File:** x/evm/keeper/msg_server.go (L283-297)
```go
	codeID := server.GetStoredPointerCodeID(ctx, msg.PointerType)
	moduleAcct := server.accountKeeper.GetModuleAddress(types.ModuleName)
	var err error
	var pointerAddr sdk.AccAddress
	if exists {
		bz, _ := json.Marshal(map[string]interface{}{})
		pointerAddr = existingPointer
		_, err = server.wasmKeeper.Migrate(ctx, existingPointer, moduleAcct, codeID, bz)
	} else {
		bz, jerr := json.Marshal(payload)
		if jerr != nil {
			return nil, jerr
		}
		pointerAddr, _, err = server.wasmKeeper.Instantiate(ctx, codeID, moduleAcct, moduleAcct, bz, fmt.Sprintf("Pointer of %s", msg.ErcAddress), sdk.NewCoins())
	}
```

**File:** example/cosmwasm/cw20/src/msg.rs (L87-94)
```rust
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvmMsg {
    DelegateCallEvm {
        to: String,
        data: String, // base64 encoded
    },
}
```
