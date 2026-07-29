### Title
Governance pause (`pair.Enabled`) on native ERC20 token-pair conversion is not enforced in the IBC timeout/error-ack re-conversion path - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`x/erc20` gates every conversion between a native Cosmos coin and its ERC20 representation on the token pair's `Enabled` flag, which governance can flip off via `ToggleConversion` to emergency-pause a compromised or misbehaving token pair. This "pause" check is enforced in the direct message handlers and in the `OnRecvPacket` IBC callback, but is missing in `ConvertCoinToERC20FromPacket`, which is invoked by both `OnAcknowledgementPacket` (on error ack) and `OnTimeoutPacket`.

### Finding Description
The pause invariant `pair.Enabled` is checked in two places:
- `MintingEnabled`, called by both `ConvertERC20` and `ConvertCoin` message handlers, explicitly rejects conversion when `!pair.Enabled`: [1](#0-0) 
- `OnRecvPacket` explicitly no-ops when the pair is disabled before calling `MintingEnabled`/`ConvertCoinNativeERC20`: [2](#0-1) 

However, `ConvertCoinToERC20FromPacket` — the function invoked on IBC timeout (`OnTimeoutPacket`) and on error acknowledgement (`OnAcknowledgementPacket`) — only checks the global `params.EnableErc20` flag and `IsDenomRegistered`, and calls `ConvertCoinNativeERC20` directly without ever consulting `pair.Enabled`: [3](#0-2) 

Because `RegisterERC20` is explicitly permissionless (any account can register a native ERC20 contract as a token pair): [4](#0-3)  an unprivileged attacker fully controls the reachability of this path: deploy a contract, register it as a native ERC20 pair, convert coin to ERC20/back to build an IBC-transferable coin balance, then send that coin over IBC with an attacker-chosen short timeout. If governance subsequently disables the pair via `ToggleConversion` (e.g., after detecting the registered ERC20 contract is malicious or has a broken invariant), any in-flight IBC transfer of that pair's denom that later times out or receives an error ack will still trigger `ConvertCoinToERC20FromPacket`, which re-invokes `ConvertCoinNativeERC20` — executing an EVM `transfer` call into the very contract governance intended to freeze — completely bypassing the pause.

### Impact Explanation
This breaks the pause invariant that `pair.Enabled=false` is supposed to fully halt on-chain interaction with a token pair's ERC20 contract. Governance uses `ToggleConversion` as an emergency stop for a token pair (e.g., a malicious/reentrant/broken contract registered permissionlessly via `RegisterERC20`). Because the IBC timeout/error-ack path is not gated by this same check, a paused pair's ERC20 contract can still be re-invoked via `ConvertCoinNativeERC20`'s `CallEVM("transfer", ...)`, re-enabling the exact interaction the pause was meant to prevent and undermining the accounting/asset-safety guarantee that disabling a pair stops all further minting/unescrow flows into it.

### Likelihood Explanation
Reachable entirely by an unprivileged user: token pair registration is permissionless, conversion between coin/ERC20 is a normal user operation, and IBC transfer timeout/timestamp values are attacker-controlled on their own outgoing transfer. The only external dependency is governance choosing to disable the pair while a transfer is in flight — a realistic incident-response scenario this pause mechanism exists to support.

### Recommendation
Add the same `pair.Enabled` (and ideally the full `MintingEnabled` checks) guard in `ConvertCoinToERC20FromPacket` before calling `ConvertCoinNativeERC20`, mirroring the check already present in `OnRecvPacket`, so that a disabled pair cannot be reconverted through the timeout/error-ack path either.

### Proof of Concept
1. Attacker calls `MsgRegisterERC20` with a permissionless call to register their own ERC20 contract as a native ERC20 token pair (`x/erc20/keeper/msg_server.go` `RegisterERC20`).
2. Attacker converts native coin to the registered ERC20 and back, or otherwise acquires the paired coin denom balance, then initiates an ICS-20 transfer of that denom to another chain with a short timeout.
3. Governance detects an issue with the registered contract and calls `MsgToggleConversion`, setting `pair.Enabled = false`.
4. The in-flight IBC packet times out; `OnTimeoutPacket` → `ConvertCoinToERC20FromPacket` is invoked (`x/erc20/keeper/ibc_callbacks.go:186-237`).
5. Since `pair.Enabled` is never checked in this path, `ConvertCoinNativeERC20` still executes an EVM call into the disabled contract, bypassing the pause governance just enacted.

### Citations

**File:** x/erc20/keeper/mint.go (L43-47)
```go
	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-123)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L216-237)
```go
	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

		// Convert from Coin to ERC20
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
```

**File:** x/erc20/keeper/msg_server.go (L324-335)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}
```
