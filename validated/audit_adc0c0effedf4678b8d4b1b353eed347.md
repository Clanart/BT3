### Title
Log injection via unsanitized `console.log` precompile input written raw to node logs - ([File: blockchain/vm/contracts.go])

### Summary
The `consoleLog` precompile in `blockchain/vm/contracts.go` decodes calldata supplied by any contract execution and writes the decoded string directly as the log **message** (not as a `key=value` context field) via `logger.Debug(decoded)`. This is analogous to CVE-2024-52337 (Tuned `instance_create` log spoofing): both cases take attacker-controlled string input and place it into a structured log stream without neutralizing embedded control/newline characters, letting an attacker forge or corrupt subsequent log lines and mislead node operators or downstream tooling that parses Kaia node logs.

### Finding Description
`consoleLog.Run` decodes the calldata into human-readable strings and logs them: [1](#0-0) 

The decoding path (`toLogString`/`decode`) supports `StringTy`/`BytesTy` parameters whose content and length are taken directly from the caller-supplied `input` bytes with only bounds checks, no character filtering: [2](#0-1) 

The resulting `decoded` string, which can contain arbitrary bytes such as `\n`, `\r`, ANSI escape sequences, or forged log fields, is passed as the **message** field of the log record rather than as a `key=value` pair. Kaia's own log formatting infrastructure (`log/format.go`) applies `escapeString()` to context **values** in `formatLogfmtValue`/`formatJsonValue`, but the message string (`r.Msg`) is written to the output stream unescaped in `TerminalFormat`: [3](#0-2) 

Because `escapeString` is only applied to `Ctx` (key/value) entries and not to `r.Msg`, any code path that feeds attacker-controlled data directly into the log message (as `consoleLog.Run` does) bypasses Kaia's existing log-escaping protection entirely. This mirrors the Tuned root cause: an API/precompile that logs caller-supplied data without sanitizing embedded newlines/control characters, enabling spoofed or split log lines.

### Impact Explanation
Any address able to submit a transaction or deploy/call a contract that hits the `CONSOLE_LOG` precompile (reachable on networks where console.log is enabled for local/dev use, and by any contract calling this fixed precompile address in general execution) can inject newline-delimited, attacker-chosen text into the node's structured log stream. This can be used to:
- Forge fake log lines (mimicking legitimate `INFO`/`ERROR` entries) to mislead operators monitoring node health/security, analogous to the Tuned advisory's log-spoofing scenario.
- Corrupt or desynchronize downstream log-parsing/monitoring/alerting pipelines that assume one log record per line.
- Potentially inject terminal control sequences if `TerminalFormat` with color is used, affecting operator terminals.

This does not directly cause fund loss or consensus divergence, but it degrades the integrity of node observability/log-based incident response — a legitimate confidentiality/integrity concern matching the CVE's Medium severity classification (log spoofing/administrator deception).

### Likelihood Explanation
High reachability: the precompile is invoked purely through EVM execution triggered by a transaction's `input` data — no special privileges, staking, or governance rights are required. Any unprivileged transaction sender who can call the `CONSOLE_LOG` precompile address (directly or via a deployed contract) controls the fully attacker-chosen string content that ends up in the log message.

### Recommendation
- Sanitize/escape the decoded `console.log` string before passing it to `logger.Debug`, e.g., strip or escape `\n`, `\r`, and non-printable/control characters, mirroring the `escapeString` treatment already applied to context values in `log/format.go`.
- Alternatively, always log decoded console output as a `key=value` context field (e.g., `logger.Debug("console.log", "msg", decoded)`) rather than as the raw message, so it goes through the existing escaping path.
- Consider applying `escapeString` (or an equivalent) to `r.Msg` itself in all `Format` implementations, not just to `Ctx` values, to close this bypass for any future/other logging call sites that pass user-controlled data as the message.

### Proof of Concept
1. Deploy or call a contract (or craft calldata directly) that triggers the `CONSOLE_LOG` precompile with a `string` parameter whose bytes include a newline sequence forging a fake log line, e.g. payload string:
   `"legit value'\n[INFO] [pretend-module] Wallet unlocked successfully addr=0xattacker"`
2. Submit the transaction to any Kaia node (no special role needed).
3. `consoleLog.Run` → `toLogString` → `decode` extracts this raw string unmodified: [2](#0-1)  and `logger.Debug(decoded)` writes it as the log message: [1](#0-0) 
4. Inspect the node's log output/log file: the injected newline splits the entry into what appears to be two separate, legitimate-looking log lines, the second one forged by the attacker — matching the Tuned-style log-spoofing pattern described in the report.

### Citations

**File:** blockchain/vm/contracts.go (L1379-1385)
```go
func (c *consoleLog) Run(input []byte, contract *Contract, evm *EVM) ([]byte, error) {
	decoded, _ := c.toLogString(input)
	if decoded != "" {
		logger.Debug(decoded)
	}
	return nil, nil
}
```

**File:** blockchain/vm/contracts.go (L1439-1452)
```go
		case common.StringTy:
			if len(params) < pos+registerSize {
				return nil, errors.New("input too short")
			}
			start := int(big.NewInt(0).SetBytes(params[pos : pos+registerSize]).Int64())
			if len(params) < start+registerSize {
				return nil, errors.New("input too short")
			}
			size := int(big.NewInt(0).SetBytes(params[start : start+registerSize]).Int64())
			if len(params) < start+size+registerSize {
				return nil, errors.New("input too short")
			}
			res = append(res, string(params[start+registerSize:start+size+registerSize]))
		case common.AddressTy:
```

**File:** log/format.go (L138-148)
```go
			if color > 0 {
				fmt.Fprintf(b, "\x1b[%dm%s\x1b[0m[%s] [%s|%s] %s %s ", color, lvl, r.Time.Format(simpleLogTimeFormat), module, location, padding, r.Msg)
			} else {
				fmt.Fprintf(b, "%s[%s] [%s|%s] %s %s ", lvl, r.Time.Format(simpleLogTimeFormat), module, location, padding, r.Msg)
			}
		} else {
			if color > 0 {
				fmt.Fprintf(b, "\x1b[%dm%s\x1b[0m[%s] [%s] %s ", color, lvl, r.Time.Format(simpleLogTimeFormat), module, r.Msg)
			} else {
				fmt.Fprintf(b, "%s[%s] [%s] %s ", lvl, r.Time.Format(simpleLogTimeFormat), module, r.Msg)
			}
```
