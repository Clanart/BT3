### Title
Log injection via unsanitized transaction input in the VMLOG precompiled contract - (File: blockchain/vm/contracts.go)

### Summary
The `vmLog` precompiled contract, reachable at address `0x09` (or `0x3fd` post-Istanbul), writes attacker-controlled call data directly into the node's `vm.log` file without sanitizing embedded newline or control characters, mirroring the CVE-2020-5225 log-injection bug class (unsanitized identifier written verbatim into a log file).

### Finding Description
Any account can call the `vmLog` precompile from a smart contract (or as a top-level call if targeting a contract that forwards the call) with arbitrary `input` bytes. `Run` builds a log line by concatenating a fixed prefix with the raw, unsanitized `input` and appends it to the `vm.log` file when `VMLogTarget` includes `VMLogToFile`: [1](#0-0) 

The write path goes through `debug.Handler.WriteVMLog`, which does nothing more than append `msg + "\n"` to the open file handle: [2](#0-1) 

Because `input` is not filtered for `\n`, `\r`, or other control/format characters before being embedded between `msg=` and the trailing newline, a caller can supply input bytes containing newline sequences to terminate the intended log entry early and inject arbitrary additional "log lines" (including fabricated `tx=`, `caller=`, `msg=` fields) into the node's persisted `vm.log` file. This is directly analogous to the SimpleSAMLphp `reportID` issue: an externally supplied identifier/content field is written to a log file without newline sanitization, enabling forged log entries.

### Impact Explanation
This does not enable direct value movement, state divergence, or consensus impact — the precompile's `Run` returns `nil, nil` and does not mutate EVM state (`blockchain/vm/contracts.go:827-837`), so it does not affect the state transition, gas accounting, or block validity. The impact is confined to falsifying the operator-facing `vm.log` file (log forgery / injection), which can be used to plant misleading entries, hinder incident forensics, or spoof `tx=`/`caller=`/`msg=` fields that operators or downstream log-parsing tooling might trust. There is no unauthorized value transfer, fee/fee-delegation abuse, gasless/auction theft, reward redirection, or invalid transaction/block acceptance resulting from this bug.

### Likelihood Explanation
The precompile is trivially reachable by any unprivileged transaction sender able to invoke it (directly or via a simple contract call) with crafted input bytes; no special privileges are required. However, exploitation only has effect on nodes/operators that have explicitly enabled `VMLogToFile` (`params.VMLogTarget`), which is an opt-in debugging feature, not default node behavior — this narrows real-world exposure.

### Recommendation
Sanitize or escape `input` before writing to the vmlog file in `vmLog.Run` (`blockchain/vm/contracts.go:827-837`) and in `WriteVMLog` (`api/debug/api.go:344-354`) — e.g., strip or escape `\n`/`\r` characters (similar to the `strconv.QuoteToASCII` treatment already applied to the stdout path at `contracts.go:834`) before appending to the file, ensuring one input always maps to exactly one log line.

### Proof of Concept
1. Deploy or call a contract that invokes the precompile at address `0x09` (pre-Istanbul) or `0x3fd` (post-Istanbul) with `input` bytes containing:
   `"legit message\ntx=0xdeadbeef... caller=0xattacker msg=FAKE ENTRY"`.
2. With a node configured with `--vmlog target=file` (or equivalent) so `params.VMLogTarget & params.VMLogToFile != 0`, the `Run` function (`blockchain/vm/contracts.go:827-831`) prepends `"tx=... caller=... msg="` and writes `prefix + string(input)` verbatim via `WriteVMLog`.
3. Because the embedded `\n` is not escaped, the resulting `vm.log` file contains two lines instead of one — the second line entirely attacker-controlled — demonstrating log injection. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** blockchain/vm/contracts.go (L826-837)
```go
// Runs the vmLog contract.
func (c *vmLog) Run(input []byte, contract *Contract, evm *EVM) ([]byte, error) {
	if (params.VMLogTarget & params.VMLogToFile) != 0 {
		prefix := "tx=" + evm.StateDB.GetTxHash().String() + " caller=" + contract.CallerAddress.String() + " msg="
		debug.Handler.WriteVMLog(prefix + string(input))
	}
	if (params.VMLogTarget & params.VMLogToStdout) != 0 {
		logger.Debug("vmlog", "tx", evm.StateDB.GetTxHash().String(),
			"caller", contract.CallerAddress.String(), "msg", strconv.QuoteToASCII(string(input)))
	}
	return nil, nil
}
```

**File:** api/debug/api.go (L344-354)
```go
// WriteVMLog writes msg to a vmlog output file.
func (h *HandlerT) WriteVMLog(msg string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.vmLogFile != nil {
		if _, err := h.vmLogFile.WriteString(msg + "\n"); err != nil {
			// Since vmlog is a debugging feature, write failure can be treated as a warning.
			logger.Warn("Failed to write to a vmlog file", "msg", msg, "err", err)
		}
	}
}
```

**File:** blockchain/vm/precompiles.go (L54-66)
```go
var PrecompiledContractsByzantium = map[common.Address]PrecompiledContract{
	common.BytesToAddress([]byte{1}):  &ecrecover{},
	common.BytesToAddress([]byte{2}):  &sha256hash{},
	common.BytesToAddress([]byte{3}):  &ripemd160hash{},
	common.BytesToAddress([]byte{4}):  &dataCopy{},
	common.BytesToAddress([]byte{5}):  &bigModExp{eip2565: false, eip7823: false, eip7883: false},
	common.BytesToAddress([]byte{6}):  &bn256AddByzantium{},
	common.BytesToAddress([]byte{7}):  &bn256ScalarMulByzantium{},
	common.BytesToAddress([]byte{8}):  &bn256PairingByzantium{},
	common.BytesToAddress([]byte{9}):  &vmLog{},
	common.BytesToAddress([]byte{10}): &feePayer{},
	common.BytesToAddress([]byte{11}): &validateSender{},
}
```
