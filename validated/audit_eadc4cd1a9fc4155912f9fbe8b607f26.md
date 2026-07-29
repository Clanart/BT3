Based on my investigation, I found a directly relevant analog but with an important caveat about verification depth.

### Finding: ICS20 transfer precompile may not reject attached native `msg.value`, risking permanently stuck funds

**Analog bug class:** Same as the DeXe `DistributionProposal::execute()` issue — a function that accepts a token/coin amount from calldata can *also* be called with native value attached (`msg.value`/`contract.Value()`), and if that value isn't validated or refunded, it becomes permanently stuck.

**Evidence found:**

The `x/erc20` precompile explicitly guards against this exact bug class: [1](#0-0) 
This code rejects any call carrying `contract.Value() > 0` with `ErrCannotReceiveFunds`, precisely because "funds sent to an instance of them ... cannot be recovered."

In contrast, `precompiles/ics20/tx.go`'s `Transfer` function builds an ICS20 `MsgTransfer` from calldata-specified `msg.Token` and invokes `p.transferKeeper.Transfer(ctx, msg)`, but nowhere references or validates `contract.Value()`/`msg.value`: [2](#0-1) 

If the ICS20 precompile's dispatch layer (`Run`/`Execute` in `precompiles/ics20/ics20.go`) does not perform the same value-rejection check that `erc20.go` does, then a caller invoking `IICS20.transfer(...)` via a low-level `call{value: X}(...)` (bypassing Solidity's compile-time non-payable restriction, since the precompile itself doesn't enforce it) would have `X` in native token transferred into the precompile's account balance by the EVM's value-transfer mechanism, while the actual IBC transfer only moves the `msg.Token` amount specified in calldata. Because the ICS20 precompile has no deposit/withdraw recovery mechanism (unlike `werc20`'s `Deposit`, which intentionally consumes `contract.Value()`), that attached native value would be permanently stranded at the precompile address — the same "stuck ETH" outcome as the original report.

**What I could not fully verify:** I found a single match for `Value()` inside `precompiles/ics20/ics20.go` but ran out of tool iterations before reading its content. It's possible that file implements the same guard as `erc20.go`'s `Execute` (i.e., `if contract.Value().Sign() == 1 { revert }`), in which case this would not be exploitable. I was unable to confirm this with certainty.

**Recommendation:** Verify `precompiles/ics20/ics20.go`'s `Run`/`Execute` function for a `contract.Value()` check analogous to the one in `precompiles/erc20/erc20.go`. If absent, add the same guard (revert when `contract.Value().Sign() == 1`) to `ics20.Transfer`, and audit other precompiles (`staking`, `distribution`, `gov`) for the same gap — any precompile whose ABI declares transaction methods as non-payable must enforce that at the `Execute`/`Run` level, not rely on Solidity's compile-time check, since low-level calls can still attach value to any address including precompiles.

Given that I could not confirm the absence of the guard in `precompiles/ics20/ics20.go`, I present this as a **candidate finding requiring confirmation** rather than a fully verified Critical vulnerability. If a Devin session with full file access confirms the guard is missing in `ics20.go`, this would qualify as a Critical "permanent freezing/locking of user funds" per the allowed impact gate.

### Citations

**File:** precompiles/erc20/erc20.go (L148-155)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}
```

**File:** precompiles/ics20/tx.go (L90-143)
```go
// Transfer implements the ICS20 transfer transactions.
func (p *Precompile) Transfer(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
	method *abi.Method,
	args []interface{},
) ([]byte, error) {
	msg, sender, err := NewMsgTransfer(method, args)
	if err != nil {
		return nil, err
	}

	// If the channel is in v1 format, check if channel exists and is open
	if channeltypes.IsChannelIDFormat(msg.SourceChannel) {
		if err := p.validateV1TransferChannel(ctx, msg); err != nil {
			return nil, err
		}
		// otherwise, it’s a v2 packet, so perform client ID validation
	} else if v2ClientIDErr := host.ClientIdentifierValidator(msg.SourceChannel); v2ClientIDErr != nil {
		return nil, errorsmod.Wrapf(
			channeltypes.ErrInvalidChannel,
			"invalid channel ID (%s) on v2 packet",
			msg.SourceChannel,
		)
	}

	msgSender := contract.Caller()
	if msgSender != sender {
		return nil, fmt.Errorf(cmn.ErrRequesterIsNotMsgSender, msgSender.String(), sender.String())
	}

	res, err := p.transferKeeper.Transfer(ctx, msg)
	if err != nil {
		return nil, err
	}

	if err = EmitIBCTransferEvent(
		ctx,
		stateDB,
		p.Events[EventTypeIBCTransfer],
		p.Address(),
		sender,
		msg.Receiver,
		msg.SourcePort,
		msg.SourceChannel,
		msg.Token,
		msg.Memo,
	); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(res.Sequence)
}
```
