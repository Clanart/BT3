## Analog found: Free-to-spam `MsgAssociate` transactions bypass fee validation

### Title
Unbounded free `MsgAssociate` transactions from fresh accounts let a malicious sender consume block gas/space without paying fees, mirroring the reported "cheap-invocation-of-subsidized-op" bug class - (File: `app/ante/cosmos_checktx.go`, `app/ante/evm_checktx.go`, `app/ante/evm_delivertx.go`, `x/evm/ante/preprocess.go`)

### Summary
Sei's ante pipeline classifies certain message types as "gasless" and skips minimum-fee validation for them entirely. One of these is a single-message `MsgAssociate` (or the equivalent EVM `AssociateTx`) whose sender is **not yet associated** — such a transaction only requires the target account to hold a nonzero balance (at least 1 wei), not a fee [1](#0-0) . The same fee-bypass logic is what the ante decorators consult (`IsTxGasless`, consumed from `app/ante/cosmos_checktx.go` and `app/ante/cosmos_delivertx.go`) [2](#0-1) .

On the EVM side, the equivalent check is `HandleAssociateTx`/`IsAccountBalancePositive`, which only requires "at least 1 wei to force association" before performing `AssociateAddress`, with no fee charged for the association itself [3](#0-2) . The same pattern recurs in the DeliverTx-only ante path and in the preprocess decorator, both of which associate the address without going through the normal fee-charging step for AssociateTx [4](#0-3) [5](#0-4) .

### Finding Description
This is the sei-chain analog of the "malicious keeper repeatedly invokes a subsidized function with a dust amount, and the protocol pays gas costs it can't recover" bug class described in the GMX report. Instead of an ADL keeper draining a treasury with dust `sizeDeltaUsd` calls, an unprivileged transaction sender can:

1. Generate an unlimited number of fresh EVM/Sei keypairs.
2. Fund each with a dust amount (1 wei) — negligible economic cost.
3. Submit a `MsgAssociate` / `AssociateTx` from each fresh, unassociated address.

Each such transaction is explicitly exempted from the minimum-fee check by the gasless classification [1](#0-0) , yet it still consumes real chain resources: signature verification, mempool bandwidth, block gas/space, and state writes performed by `AssociateAddresses` in the ante pipeline [6](#0-5) . Because the requirement is "not yet associated," the attacker has an effectively unbounded supply of qualifying senders (every freshly generated key is unassociated by definition), so the free-transaction vector does not saturate the way a normal per-account gasless allowance would.

This mirrors the ADL report's core defect: a class of operation that is deliberately fee-subsidized (there, gas cost was subsidized by the treasury for ADL/liquidation keepers; here, the fee requirement is waived entirely for bootstrap-association transactions) can be invoked at near-zero cost, in an unbounded loop, by whoever satisfies a cheap precondition (dust `sizeDeltaUsd` there; dust 1-wei balance and a fresh keypair here).

### Impact Explanation
Because gasless `MsgAssociate`/`AssociateTx` transactions bypass minimum-fee validation, an attacker can flood blocks with these transactions to consume block gas/space and validator processing resources without paying commensurate fees. This is a fee/resource-subsidization abuse vector consistent with the accepted impact categories (fee or refund abuse; potential block-processing delay under sustained spam) rather than a direct fund-transfer exploit. It does not by itself transfer treasury funds to the attacker (unlike the original ADL report, where the treasury directly reimburses keeper gas), but it exploits the same structural flaw: an economically-free, repeatable operation that a keeper/attacker fully controls the invocation rate of.

### Likelihood Explanation
Likelihood is High for an attacker with modest capital: generating keypairs is free, and funding each with 1 wei is economically negligible compared to the resource cost the network absorbs per accepted transaction (signature verification, ante-handler state writes, block space). The precondition ("not yet associated") is trivially satisfiable indefinitely since new keys are always unassociated.

### Recommendation
- Require a minimum fee (or a rate-limited allowance per block/IP/account) even for the "sender not yet associated" gasless carve-out in `IsTxGasless`, rather than a blanket fee exemption.
- Consider bounding the free-association fast path with a per-block cap or an increasing cost curve (e.g., escalating minimum wei/fee requirement) to remove the unbounded, zero-marginal-cost spam vector, mirroring the original report's suggestion of "add a minimum amount" to remove the free-dust-invocation incentive.
- Add a spam-prevention counter for gasless message types analogous to the oracle module's existing `CheckAndSetSpamPreventionCounter` pattern [7](#0-6) , applied per-sender or per-block for association transactions.

### Proof of Concept
Not independently verified against running chain state; the exact `IsTxGasless` implementation (`app/antedecorators/gasless.go`) was not resolvable in the code index (only its description in `REVIEW.md` and its call sites in the ante chain were available) [1](#0-0) . Conceptually:
1. Generate N fresh EVM keypairs.
2. Fund each with 1 wei via a single bootstrap transfer.
3. Submit N `AssociateTx`/`MsgAssociate` transactions, one per keypair, each satisfying `IsAccountBalancePositive` and bypassing fee checks [8](#0-7) .
4. Observe that all N transactions land without any fee being charged, consuming block gas.

**Uncertainty**: I could not retrieve the full source of `app/antedecorators/gasless.go` (the file/function backing `IsTxGasless`) due to codebase-index size limits — its exact conditions were only visible second-hand via `REVIEW.md`. If a Devin session with full repo access is available, this should be verified directly against `IsTxGasless` and any existing rate-limiting before treating this as conclusively unmitigated.

### Citations

**File:** REVIEW.md (L65-85)
```markdown
## 3. Some Cosmos transactions are gasless: don't require `--fees` on them

Do not flag a `seid tx` invocation (in tests, scripts, or docs) as broken for
omitting `--fees` before checking whether the message type is gasless. Sei's
ante handlers classify certain transactions as gasless and skip minimum-fee
validation entirely for them, so `minimum-gas-prices`-based fee arithmetic
(`ceil(min-gas-price × gas-limit)`) does not apply.

The classification lives in `IsTxGasless` (`app/antedecorators/gasless.go`)
and is consumed by both the CheckTx and DeliverTx ante paths
(`app/ante/cosmos_checktx.go`, `app/ante/cosmos_delivertx.go`), where
`CheckAndChargeFees` returns before any fee comparison when the transaction
is gasless. As of this writing the gasless set is:

- a single-message `MsgAssociate` (`seid tx evm native-associate`) whose
  sender is **not yet associated** — the common case in bootstrap helpers and
  association tests; the sender only needs a nonzero balance (at least 1 wei),
  not a fee, and
- `MsgAggregateExchangeRateVote` from a validator without a vote in the
  current window.

```

**File:** app/ante/evm_checktx.go (L182-204)
```go
func HandleAssociateTx(ctx sdk.Context, ek *evmkeeper.Keeper, atx *ethtx.AssociateTx, readOnly bool) (sdk.Context, error) {
	V, R, S := atx.GetRawSignatureValues()
	V = new(big.Int).Add(V, utils.Big27)
	// Hash custom message passed in
	customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
	evmAddr, seiAddr, seiPubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
	if err != nil {
		return ctx, err
	}
	_, isAssociated := ek.GetEVMAddress(ctx, seiAddr)
	if isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	}
	if !IsAccountBalancePositive(ctx, ek, seiAddr, evmAddr) {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
	}
	if !readOnly {
		if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
			return ctx, err
		}
	}
	return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil
}
```

**File:** app/ante/evm_delivertx.go (L34-36)
```go
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		return HandleAssociateTx(ctx, ek, atx, false)
	}
```

**File:** x/evm/ante/preprocess.go (L76-97)
```go
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
```

**File:** x/oracle/keeper/keeper.go (L584-593)
```go
func (k Keeper) CheckAndSetSpamPreventionCounter(ctx sdk.Context, validatorAddr sdk.ValAddress) error {
	mtx, _ := k.spamPreventionCounterMtxMap.LoadOrStore(validatorAddr.String(), &sync.Mutex{})
	mtx.Lock()
	defer mtx.Unlock()
	if k.getSpamPreventionCounter(ctx, validatorAddr) == ctx.BlockHeight() {
		return sdkerrors.Wrap(sdkerrors.ErrAlreadyExists, fmt.Sprintf("the validator has already submitted a vote at the current height=%d", ctx.BlockHeight()))
	}
	k.setSpamPreventionCounter(ctx, validatorAddr)
	return nil
}
```
