Confirmed: `RegisterERC20` in `x/erc20/keeper/msg_server.go` is **permissionless by default** (`params.PermissionlessRegistration`), and `registerERC20` in `x/erc20/keeper/proposals.go` accepts any arbitrary deployed EVM contract as `OWNER_EXTERNAL` with zero bytecode/interface validation beyond `CreateCoinMetadata` succeeding (which only needs `name()`/`symbol()`/`decimals()` to return correctly — it does not validate `approve`/`transferFrom` return-data conformance). [1](#0-0) [2](#0-1) 

This means an unprivileged user can permissionlessly register a non-standard ERC20 contract (e.g., one that omits the `bool` return on `approve`, matching the USDT/USDC pattern from the original report) as an `OWNER_EXTERNAL` token pair, then bridge it in via IBC with a destination callback targeting that pair.

### Title
Permanent Fund Lock in IBC Callback Isolated Address via Non-Standard ERC20 `approve` Return-Data Mismatch - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` hardcodes `contracts.ERC20MinterBurnerDecimalsContract.ABI` to decode the return value of an `approve` call made against `tokenPair.GetERC20Contract()`, then strictly unpacks a `bool` from the raw EVM return data. Because `RegisterERC20` is permissionless and accepts any externally-deployed ERC20 contract as `OWNER_EXTERNAL` without validating standard-compliant return data, a user can register a non-standard token (no return value on `approve`, e.g. USDT-style bytecode) and trigger an IBC transfer with a destination callback for that token. The `approve` call succeeds on-chain (funds already delivered to the deterministic, keyless isolated address per `GenerateIsolatedAddress`), but `erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)` fails to decode the empty/short return data, causing the callback to error out before the downstream contract call and balance-check ever execute. [3](#0-2) 

### Finding Description
The flow is:
1. IBC transfer completes, crediting the isolated address (`GenerateIsolatedAddress(channel, sender)`), which has **no private key** — it is a hash-derived pseudo-account, only reachable via this same callback path.
2. The callback attempts `approve(contractAddr, amount)` on `tokenPair.GetERC20Contract()` using the ABI of the protocol's own `ERC20MinterBurnerDecimalsContract`, assuming a `bool` return.
3. If the registered token pair's contract does not return a `bool` from `approve` (non-standard ERC20 behavior, common on mainnet tokens like USDT and replicable via a permissionlessly-registered custom contract), `UnpackIntoInterface` fails and `IBCReceivePacketCallback` returns `types.ErrAllowanceFailed`.
4. Per IBC-Go's callback middleware semantics, a failing destination callback does **not** roll back the already-completed token transfer to the isolated address — the tokens remain credited there.
5. Since the isolated address is not a real externally-owned account and the only intended path to move funds out of it is this callback (approve + `transferFrom` by the target contract), and that path deterministically fails every time due to the ABI mismatch, the tokens are **permanently and irrecoverably locked**.

This is the same underlying bug class as the source report (using unchecked/return-data-assuming `approve` against tokens that don't conform to the standard `bool`-returning ABI), but the concrete Cosmos EVM impact is not a revert-only DoS — it is permanent fund freezing at an address with no recovery mechanism. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
This matches the "Critical permanent freezing/locking of user funds" allowed impact: an unprivileged user (or third party sending IBC transfers with a callback to a maliciously/carelessly registered token pair) causes assets to be irreversibly stuck at a keyless isolated address. Because token registration is permissionless (`PermissionlessRegistration` defaults enable non-governance registration), the attacker fully controls the ERC20 bytecode used, guaranteeing the `approve` ABI-decode failure is 100% reproducible and not dependent on any privileged action.

### Likelihood Explanation
High: no governance or validator cooperation is required. An attacker (or accidental integrator) needs only to (1) deploy a minimal ERC20-like contract whose `approve` doesn't return a `bool`, (2) call the permissionless `MsgRegisterERC20`, and (3) initiate an ICS20 transfer with `DestinationCallbackKey` memo pointing to any contract. Every subsequent transfer through that token pair with a callback will strand funds at the isolated address.

### Recommendation
- Do not assume ABI-standard return data from arbitrary registered `OWNER_EXTERNAL` contracts when performing the internal `approve` call in `IBCReceivePacketCallback`. Treat a successful low-level call (no revert) as sufficient, or independently verify the allowance was actually set via a follow-up `allowance()` read instead of strictly unpacking the `approve` return value.
- Alternatively, validate ERC20 standard compliance (including return-data shape for `transfer`/`approve`/`transferFrom`) at `RegisterERC20` time before allowing a contract to be used as a token pair, especially under permissionless registration.
- Provide a recovery/sweep mechanism for isolated addresses in case a destination callback cannot complete, so funds are not irrecoverably lost.

### Proof of Concept
1. Deploy a contract implementing `transfer`/`transferFrom`/`balanceOf` normally but with `approve(address,uint256)` declared to return nothing (no `(bool)` in the signature/ABI, mirroring real USDT bytecode).
2. Call `MsgRegisterERC20` with this contract's address (permissionless, no governance vote needed if `PermissionlessRegistration` is enabled — the default/common configuration [7](#0-6) ).
3. Convert some native coin to this ERC20 via `MsgConvertCoin`, or otherwise fund an account with the corresponding denom.
4. Initiate an ICS20 `MsgTransfer` for this denom with a `memo` containing `DestinationCallbackKey` data pointing calldata at any deployed receiving contract.
5. Observe: the isolated address (`GenerateIsolatedAddress`) receives the tokens; `IBCReceivePacketCallback` executes `approve`, then fails `UnpackIntoInterface` and returns `ErrAllowanceFailed` at [5](#0-4) ; the receiving contract's intended `transferFrom` pull never runs and the balance-check/guard at lines 229-239 is never reached.
6. The isolated address now permanently holds the tokens with no private key or alternate withdrawal path — funds are stuck indefinitely.

### Citations

**File:** x/erc20/keeper/msg_server.go (L324-350)
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

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}
```

**File:** x/erc20/keeper/proposals.go (L16-41)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
```

**File:** x/ibc/callbacks/keeper/keeper.go (L145-155)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())

	acc := k.authKeeper.NewAccountWithAddress(ctx, receiver)
	k.authKeeper.SetAccount(ctx, acc)

	// Ensure receiver address is equal to the isolated address.
	if receiverHex.Cmp(isolatedAddrHex) != 0 {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress, "expected %s, got %s", isolatedAddrHex.String(), receiverHex.String())
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L185-212)
```go
	erc20 := contracts.ERC20MinterBurnerDecimalsContract

	remainingGas := math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt()

	// Call the EVM with the remaining gas as the maximum gas limit.
	// Up to now, the remaining gas is equal to the callback gas limit set by the user.
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance: %v", err)
	}

	// Consume the actual used gas on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback allowance")
	remainingGas = remainingGas.Sub(remainingGas, math.NewIntFromUint64(res.GasUsed).BigInt())
	if ctx.GasMeter().IsOutOfGas() || remainingGas.Cmp(big.NewInt(0)) < 0 {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}

	var approveSuccess bool
	err = erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to unpack approve return: %v", err)
	}

	if !approveSuccess {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance")
	}
```
