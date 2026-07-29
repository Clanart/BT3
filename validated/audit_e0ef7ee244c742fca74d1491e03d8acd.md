### Title
Permissionless ERC20 registration allows attacker-controlled token contracts to fake `balanceOf`/`transfer` and mint unlimited native coins via `ConvertERC20` - (File: x/erc20/keeper/msg_server.go)

### Summary
`x/erc20` supports `PermissionlessRegistration`, which allows any unprivileged account to register an arbitrary ERC20 contract as a token pair via `MsgRegisterERC20` [1](#0-0) . Once registered as `OWNER_EXTERNAL`, the paired contract is fully trusted by `convertERC20IntoCoinsForNativeToken`, which mints real bank-module coins based solely on that untrusted contract's self-reported `balanceOf`/`transfer` return values [2](#0-1) . This mirrors the external report's root cause: an unprivileged actor can attach an arbitrary/malicious asset (Vault→Staking contract in the original report; ERC20 contract→token pair here) to a shared privileged accounting flow, and manipulate that asset's self-controlled logic to extract value from the system.

### Finding Description
`RegisterERC20` is exposed as a normal message; when `params.PermissionlessRegistration` is `true`, no authority check is performed at all: `k.validateAuthority` is skipped entirely [1](#0-0) . This lets any signer register any hex-address contract, including one they wrote and deployed themselves, as a `TokenPair` with `OWNER_EXTERNAL` [3](#0-2) .

Once registered, calling `ConvertERC20` on that contract routes to `convertERC20IntoCoinsForNativeToken`, which:
1. Reads `balanceToken` before the transfer via `k.BalanceOf(...)`, which just calls the *contract's own* `balanceOf` implementation with no independent verification [4](#0-3) .
2. Calls `transfer(moduleAddress, amount)` on the same attacker-controlled contract and trusts its return value/emitted Transfer event [5](#0-4) .
3. Re-reads `balanceOf` after the call and checks `balanceTokenAfter == balanceToken + amount` [6](#0-5) .
4. If that self-reported invariant "passes," the module calls `bankKeeper.MintCoins` and sends the newly minted native coins to the receiver [7](#0-6) .

Because the entire "escrow" side of this accounting is delegated to a contract the attacker fully controls (deployed and registered by the attacker themselves), the attacker can make `balanceOf` and `transfer` return whatever values are needed to satisfy the invariant check without any real token custody ever changing hands (e.g., `balanceOf` simply always returns `initial + cumulativeConverted`, or `transfer` unconditionally emits a valid `Transfer` event/returns `true` while doing nothing). The check at step 3 only verifies internal consistency of the malicious contract's own state, not that any real value was locked. This produces unauthorized minting of real, spendable native bank-module coin (`pair.Denom`), which is fully fungible, transferable and spendable exactly like ordinary native balance/IBC-escrow-backed value — directly hitting the "Critical unauthorized minting… of spendable user value across native balances" impact gate.

This is architecturally the same defect pattern as the original report: an unprivileged user is allowed to attach/bind an arbitrary, self-authored asset (Vault↔Staking pairing there, ERC20↔TokenPair pairing here) to a privileged value-accounting subsystem, and the subsystem then trusts state/return-values fully controlled by that same attacker to authorize value creation/extraction.

### Impact Explanation
An attacker can mint unlimited amounts of the native Cosmos coin denom corresponding to their malicious token pair, and — since ERC20 conversion is bidirectional and the resulting coin is a normal bank-module coin — this coin is spendable, transferable, usable in IBC transfers, staking, and any other module that accepts the native denom. This is unauthorized, irreversible creation of spendable value with no cap, satisfying the "Critical unauthorized minting… of spendable user value" impact category.

### Likelihood Explanation
Requires only: (1) `PermissionlessRegistration=true` (a governance-configurable, not a hard-coded-disabled, parameter — its purpose is explicitly to let this be permissionless) and (2) the attacker being able to deploy and register their own trivial malicious ERC20 contract, both of which are ordinary unprivileged EVM/tx actions. No relayer, validator, or governance action is required, making this trivially reachable whenever the parameter is enabled. Likelihood is High if `PermissionlessRegistration` is enabled in the deployed chain configuration; this could not be fully confirmed from the indexed code (default value for the parameter was not located before the search budget was exhausted), so it should be validated against the deployment's actual genesis/params before treating this as universally exploitable.

### Recommendation
- Do not fully trust externally-owned ERC20 contracts' self-reported `balanceOf`/`transfer` semantics as the sole gate for minting native coins; require additional invariants that are independent of the token's own logic (e.g., verify the contract bytecode matches a known-safe template, or use a well-audited transfer/allowance pattern with real escrow verified against a canonical, unforgeable EVM state read rather than an ABI call into the same untrusted contract).
- Reconsider allowing permissionless registration of *arbitrary* externally-owned contracts into the mint-bearing conversion pipeline; at minimum, cap/rate-limit mintable amounts per externally-owned token pair, or require a bond/registration fee proportional to conversion volume, and add strict pre-registration validation of contract bytecode (e.g., disallow arbitrary custom logic, or require it be a minimal, verified proxy/clone of a canonical ERC20 implementation).
- Add a circuit breaker that disables a token pair automatically if the balance-invariant check for that pair ever fails or behaves anomalously across converts, and consider making `OWNER_EXTERNAL` conversions asymmetric (e.g., disallow minting native coin against arbitrary externally-owned contracts, only allow converting FROM native coin TO them).

### Proof of Concept
Conceptual PoC (Go/Solidity, cannot be executed in this environment):
1. Attacker deploys `EvilToken.sol` implementing `balanceOf(address)` to always return `k.BalanceOf`'s expected value (`priorBalance + amount`) regardless of actual token movement, and `transfer(address,uint256)` to simply emit a valid `Transfer` event and return `true` without moving any real balance.
2. Attacker (any account, no special privileges) submits `MsgRegisterERC20{Signer: attacker, Erc20Addresses: [EvilToken address]}`. Since `params.PermissionlessRegistration == true`, `RegisterERC20` in `x/erc20/keeper/msg_server.go` skips the authority check and creates a `TokenPair` with `OWNER_EXTERNAL` for `EvilToken`.
3. Attacker submits `MsgConvertERC20{ContractAddress: EvilToken, Amount: <huge amount>, Sender: attacker, Receiver: attacker}`.
4. `convertERC20IntoCoinsForNativeToken` calls `EvilToken.balanceOf(module)` (returns fabricated value), calls `EvilToken.transfer(module, amount)` (does nothing but reports success), re-reads `balanceOf` (returns the fabricated post-transfer value matching the expected invariant), then calls `bankKeeper.MintCoins` and sends the minted coin to attacker.
5. Attacker now holds arbitrarily large amounts of a real, spendable native coin denom, mintable repeatedly with no real collateral ever provided.

### Citations

**File:** x/erc20/keeper/msg_server.go (L71-140)
```go
func (k Keeper) convertERC20IntoCoinsForNativeToken(
	ctx sdk.Context,
	pair types.TokenPair,
	msg *types.MsgConvertERC20,
	receiver sdk.AccAddress,
	sender common.Address,
) (*types.MsgConvertERC20Response, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()
	balanceCoin := k.bankKeeper.GetBalance(ctx, receiver, pair.Denom)
	balanceToken := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceToken == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
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

**File:** x/erc20/keeper/proposals.go (L16-42)
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
}
```

**File:** x/erc20/keeper/evm.go (L137-159)
```go
// BalanceOf queries an account's balance for a given ERC20 contract
func (k Keeper) BalanceOf(
	ctx sdk.Context,
	abi abi.ABI,
	contract, account common.Address,
) *big.Int {
	res, err := k.evmKeeper.CallEVM(ctx, abi, types.ModuleAddress, contract, false, nil, "balanceOf", account)
	if err != nil {
		return nil
	}

	unpacked, err := abi.Unpack("balanceOf", res.Ret)
	if err != nil || len(unpacked) == 0 {
		return nil
	}

	balance, ok := unpacked[0].(*big.Int)
	if !ok {
		return nil
	}

	return balance
}
```
