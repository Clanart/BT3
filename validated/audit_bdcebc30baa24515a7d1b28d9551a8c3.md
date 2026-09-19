## Title
Missing zero-address (and unassociated-address) validation in Bank precompile's `sendNative` leads to permanently lost usei/wei - (File: `precompiles/bank/bank.go`)

## Summary
The Bank precompile's `sendNative` function lets an EVM caller send native `usei`/`wei` funds to an arbitrary bech32-encoded Sei address string. Unlike `send()` (and the distribution precompile's `setWithdrawAddress`), which resolve recipients through `accAddressFromArg`/`GetSeiAddressFromArg` and explicitly reject the zero `common.Address{}`, `sendNative` parses the recipient directly from a raw string via `sdk.AccAddressFromBech32` with no check against the "zero" `AccAddress` (32 zero bytes). This mirrors the reported UXD `transferETH` bug: a caller-supplied destination is used to move value without validating it isn't a null/unspendable address, so funds sent there are permanently unrecoverable.

## Finding Description
In `precompiles/bank/bank.go` (and every legacy version, e.g. `precompiles/bank/legacy/v640/bank.go`), `sendNative` accepts `args[0]` as a `string`, validates only that it is non-empty, and converts it with `sdk.AccAddressFromBech32`: [1](#0-0) 

This differs from the pattern used for EVM-address recipients elsewhere in the precompile suite (`send`, and `distribution.setWithdrawAddress`), which route through a helper that explicitly checks for the zero address: [2](#0-1) 

Because `sendNative`'s recipient comes from a raw bech32 string rather than a `common.Address`, that zero-address guard never applies to it. If a caller supplies the bech32 encoding of the all-zero 20-byte `AccAddress` (a syntactically valid, decodable address that corresponds to no known private key), `AccAddressFromBech32` succeeds, `SendCoinsAndWei` executes the transfer, and a fresh account is even auto-created for it: [3](#0-2) 

No downstream check (in `x/bank` `SendCoinsAndWei`) rejects sending to this unowned address either.

## Impact Explanation
Any `usei`/`wei` sent to the zero `AccAddress` via `sendNative` is permanently locked — nobody holds the corresponding private key, so the funds can never be spent, matching the "concrete fund loss or permanent freezing" acceptance criterion. Because `sendNative` is a `payable`, publicly callable function reachable by any unprivileged EVM transaction sender/contract, this is a direct, reachable fund-loss vector, not a privileged-only issue.

## Likelihood Explanation
Triggering this only requires an EVM account (or a contract acting on a user's behalf, e.g. a bridging/wallet integration that constructs the bech32 string programmatically) to call `sendNative` with a malformed/zero-value Sei address string — e.g. due to a bug in address derivation, a copy-paste error, or malicious frontend/dApp tricking a user. Since `send()`'s address argument (an actual `common.Address`) is protected against the zero value but `sendNative`'s string argument is not, this is an inconsistent and overlooked validation gap analogous exactly to the reported `UXDTimelockController.transferETH` issue.

## Recommendation
In `sendNative`, after decoding `receiverSeiAddr` via `sdk.AccAddressFromBech32`, add an explicit check rejecting the zero/empty `AccAddress` (e.g. `if receiverSeiAddr.Empty() || receiverSeiAddr.Equals(sdk.AccAddress{}) { return nil, 0, errors.New("invalid addr") }`), mirroring the guard already present for `common.Address{}` in `GetSeiAddressFromArg`/`accAddressFromArg`. Apply the fix consistently across all `precompiles/bank/legacy/*/bank.go` copies (or ensure only the currently active version needs patching, if legacy code paths are no longer invoked).

## Proof of Concept
1. An EVM account calls `BANK_CONTRACT.sendNative{value: 1_000_000000000000}("sei1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqhq88u9")` — the bech32 encoding of the 20 zero bytes `AccAddress`.
2. `sendNative` in `precompiles/bank/bank.go`/legacy variants validates `value != 0` and that the string is non-empty, then calls `sdk.AccAddressFromBech32`, which succeeds because the string is a valid bech32 encoding.
3. `p.bankKeeper.SendCoinsAndWei` transfers the usei/wei to the zero `AccAddress`; since no account previously existed, `p.accountKeeper.SetAccount` creates one.
4. The funds now sit at an address with no known private key and can never be withdrawn — permanent loss. [4](#0-3)

### Citations

**File:** precompiles/bank/legacy/v640/bank.go (L187-228)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, 0, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, 0, err
	}

	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
	if !accExists {
		defer metrics.SafeTelemetryIncrCounter(1, "new", "account")
		p.accountKeeper.SetAccount(ctx, p.accountKeeper.NewAccountWithAddress(ctx, receiverSeiAddr))
	}
```

**File:** precompiles/common/precompiles.go (L357-363)
```go
func GetSeiAddressFromArg(ctx sdk.Context, arg interface{}, evmKeeper putils.EVMKeeper) (sdk.AccAddress, error) {
	addr := arg.(common.Address)
	if addr == (common.Address{}) {
		return nil, errors.New("invalid addr")
	}
	return GetSeiAddressByEvmAddress(ctx, addr, evmKeeper)
}
```
