### Title
Permissionless registration of rebasing/fee-on-transfer ERC20 tokens as native token pairs can permanently strand coin-side balances - (File: `x/erc20/keeper/msg_server.go`, `x/erc20/keeper/proposals.go`)

### Summary
This is analogous to the `i_arbiterFee` bug: the escrow contract assumed a fixed value would always be backed by the ERC20 contract's balance, and a rebasing token broke that assumption, locking funds. In this codebase, `x/erc20`'s native-ERC20 token pair conversion logic (`ConvertCoinNativeERC20` / `convertERC20IntoCoinsForNativeToken`) assumes a strict 1:1, non-rebasing relationship between the amount of native `sdk.Coin` minted/escrowed and the ERC20 balance held by `types.ModuleAddress`. Because `RegisterERC20` can be permissionless (`params.PermissionlessRegistration`), any unprivileged user can register an arbitrary ERC20 contract - including a deflationary/rebasing/fee-on-transfer token - as a token pair.

### Finding Description
`RegisterERC20` in `x/erc20/keeper/msg_server.go:326-362` only requires `k.validateAuthority(req.Signer)` when `params.PermissionlessRegistration` is false; when the parameter is true, any signer can call it to register any ERC20 contract address, without any validation that the contract behaves like a standard, non-rebasing ERC20 [1](#0-0) . `registerERC20` in `x/erc20/keeper/proposals.go:18-42` performs no checks on transfer semantics, fee-on-transfer behavior, or balance elasticity of the contract before minting a corresponding bank denom with `CreateCoinMetadata` [2](#0-1) .

Once registered, `ConvertERC20` → `convertERC20IntoCoinsForNativeToken` escrows the ERC20 tokens into `types.ModuleAddress` via an EVM `transfer` call, then mints the same nominal amount of native coin and sends it to the receiver, with a strict invariance check (`ErrBalanceInvariance`) comparing before/after ERC20 balance of `types.ModuleAddress` [3](#0-2) . This invariance check protects a single conversion transaction, but there is no invariant enforced across time: if the underlying ERC20 token's balance for `types.ModuleAddress` can shrink independently of any x/erc20-driven transfer (e.g., because the token is a negative-rebasing token, or is fee-on-transfer/deflationary in ways not triggered by this call, or has an admin-controlled rebase/burn function), the module's escrowed ERC20 balance backing already-minted native coins can fall below the coin supply that was minted 1:1 at conversion time.

The reverse path, `ConvertCoinNativeERC20` (`x/erc20/keeper/msg_server.go:237-306`), escrows native coin from the sender to the module account and then calls `evmKeeper.CallEVM(... "transfer", receiver, amount.BigInt())` from `types.ModuleAddress`, followed by the same strict balance-invariance check [4](#0-3) . If the module's actual ERC20 balance has been reduced below the nominal 1:1 backing (due to rebasing/deflation happening outside this exact call), this transfer will either fail outright (module lacks sufficient tokens) or the invariance check will revert the transaction with `ErrBalanceInvariance`. Because the check is strict equality against the expected balance delta, and there is no fallback (e.g., partial redemption, socialized loss, or governance-controlled remediation), coin holders holding the native representation of that token pair become permanently unable to redeem their coins for the underlying ERC20 — the coins remain circulating in `x/bank` but are functionally un-backed and irredeemable, matching the "funds locked" impact class from the original report.

### Impact Explanation
This matches the required Critical impact: **permanent freezing/locking of user funds** and **irreversible accounting corruption of spendable user value**. Native coin holders for an affected token pair would hold bank-module coins that can never again be converted back into the underlying ERC20 token once the module's escrowed balance is insufficient, because `ConvertCoinNativeERC20` will either revert on the underlying token transfer or on the `ErrBalanceInvariance` check. There is no compensating mechanism in `x/erc20` to detect or remediate a shortfall between the coin supply and the actual ERC20 balance held in escrow.

### Likelihood Explanation
Likelihood depends on: (1) `PermissionlessRegistration` being enabled (a governance-configurable parameter — need to confirm default), and (2) an attacker/token issuer registering a rebasing, deflationary, or otherwise balance-elastic ERC20 contract. Given ERC20 registration in production EVM chains is frequently permissionless by design (to allow arbitrary token bridging), and rebasing/deflationary tokens are common in DeFi, this is a realistic, low-cost, unprivileged trigger: an attacker only needs to deploy a malicious/rebasing ERC20 and call `RegisterERC20`, then have users convert into the coin representation. I was not able to fully confirm within the available context whether `PermissionlessRegistration` defaults to `true` or is gated by governance in production configs (e.g., `evmd`) — this should be verified before treating this as a confirmed production issue rather than a design consideration, since a `false` default with governance-gated permission would substantially reduce the unprivileged-trigger surface.

### Recommendation
- Before minting native coin against an ERC20 escrow, do not assume static 1:1 backing indefinitely; consider tracking actual escrowed balance versus minted-coin supply and reconciling on every conversion (e.g., recompute the conversion ratio based on current module balance vs. total minted coin supply rather than a fixed 1:1 assumption), analogous to computing the arbiter fee as a percentage of current balance rather than a fixed amount.
- Add a supply/balance invariant check tied to the actual `BalanceOf` of `types.ModuleAddress` versus outstanding coin supply for that denom, and reject/flag token pairs whose balance has diverged, ideally before they cause a stuck state (e.g., detect at each `ConvertERC20`/`ConvertCoin` call, or via a periodic on-chain check).
- Consider restricting permissionless registration to tokens that pass a standard-compliance check (e.g., a probe mint/transfer with pre/post-balance verification of the registering account beyond the immediate call, over multiple blocks, or an allowlist/governance approval step) to exclude rebasing, fee-on-transfer, and deflationary tokens.

### Proof of Concept
1. Ensure `params.PermissionlessRegistration` is `true` (needs verification against default `evmd` genesis params).
2. Deploy a malicious ERC20 contract that reports normal balances/transfers during registration but reduces `balanceOf(ModuleAddress)` afterward (e.g., via a rebase function callable by the token owner, or a scheduled negative rebase).
3. Call `MsgRegisterERC20` with this contract address (unprivileged, since registration is permissionless) — `x/erc20/keeper/msg_server.go:326-362`.
4. A user calls `MsgConvertERC20` to escrow tokens into `types.ModuleAddress` and mint native coin 1:1 — `x/erc20/keeper/msg_server.go:71-188`.
5. Trigger the rebase/negative-balance-adjustment on the ERC20 contract, reducing `types.ModuleAddress`'s actual token balance below the amount of native coin outstanding for that denom.
6. The user (or any other coin holder) calls `MsgConvertCoin` to redeem their native coin back to ERC20 — `x/erc20/keeper/msg_server.go:237-306`. The `evmKeeper.CallEVM` transfer either fails due to insufficient underlying balance, or succeeds partially and trips the `ErrBalanceInvariance` check, reverting the transaction and leaving the user's coins permanently non-redeemable.

Note: I could not fully verify in this pass (i) the default value of `PermissionlessRegistration` in production chain configurations, or (ii) whether any additional standard-compliance validation exists elsewhere in the registration/EVM-call pipeline that would reject non-standard rebasing tokens at registration time. These would need direct code/config confirmation (e.g., via a Devin session with full repository access) before treating this as a fully confirmed, unconditionally exploitable Critical finding.

### Citations

**File:** x/erc20/keeper/msg_server.go (L86-130)
```go
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
```

**File:** x/erc20/keeper/msg_server.go (L256-297)
```go
	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}

	// Check unpackedRet execution
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return err
		}
		if !unpackedRet.Value {
			return sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute unescrow tokens from user")
		}
	}

	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-345)
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
