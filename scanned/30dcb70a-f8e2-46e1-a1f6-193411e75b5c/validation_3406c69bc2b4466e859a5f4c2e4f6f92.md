## Analysis

The FilSnap bug is fundamentally: **untrusted attacker-influenced data is emitted verbatim into a structure that a human/consumer trusts as a well-formatted, self-consistent status display, allowing spoofed content that changes the consumer's understanding of what happened.**

The closest analog I could substantiate in Agave with local code evidence is program log spoofing via unsanitized `sol_log` content, since Agave documents and guarantees a stable, parseable log format that many downstream consumers (block explorers, bridges, exchanges, Anchor-based clients) rely on to determine per-instruction success/failure, yet the attacker-controlled message content going into that format is never sanitized for embedded control characters/newlines.

### Title
Unsanitized program log content allows spoofing of stable "invoke/success/failed" log lines returned via RPC - (File: `program-runtime/src/stable_log.rs`)

### Summary
`stable_log::program_log` wraps a fully attacker-controlled string in the fixed, documented-as-stable prefix `"Program log: {message}"` with no filtering of embedded newlines or control characters, then this array of strings is returned as-is by `getTransaction`, `simulateTransaction`, and `logsNotification`. Because the same file also emits the fixed-format `"Program {} invoke [{}]"`, `"Program {} success"`, and `"Program {} failed: {}"` lines that are documented as a stable contract for downstream consumers, a malicious unprivileged program can embed those exact substrings/newlines inside its own log message to forge extra, fake lines that a naive log-parsing consumer will treat as authoritative execution status for a program that never actually logged them.

### Finding Description
`stable_log::program_log` builds the log entry directly from caller-supplied `message: &str` with no escaping: [1](#0-0) 

The doc comment at the top of the file explicitly states the format is a stable contract for external consumers, i.e. Agave promises third parties can parse these lines: [2](#0-1) 

`program_invoke`, `program_success`, and `program_failure` use these exact same fixed formats: [3](#0-2) [4](#0-3) 

The `sol_log` syscall passes the raw BPF-VM memory bytes straight to `stable_log::program_log` after only validating UTF-8 — no filtering of `\n`, `\r`, or other control characters occurs: [5](#0-4) 

`translate_string_and_do`, used to decode the message, only checks `from_utf8`; it performs no content sanitization: [6](#0-5) 

`LogCollector::log` simply appends `message.to_string()` to the messages vector (only byte-limit truncation is applied, no character filtering), so an embedded newline in the message is stored as-is inside a single JSON array element and only becomes visible as multiple lines when the raw string is printed/rendered by a consumer.

These raw log arrays are the exact value returned via `getTransaction` (`meta.logMessages`), `simulateTransaction` (`logs`), and `logsNotification` (`logs`), as shown by their expected test payloads: [7](#0-6) [8](#0-7) 

Because a program can log arbitrary content via `sol_log` (unprivileged — any deployed program can call it) and that content is embedded unsanitized into a single array entry, an attacker can:
1. Include a literal `\n` inside their `sol_log` message.
2. Follow it with text exactly matching the stable format, e.g. `Program <victim_program_id> success` or `Program <victim_program_id> invoke [1]`.

When this single log-message string (which is really `"Program log: ...attacker text...\nProgram <victim> success\n..."`) is rendered by any consumer that treats the log array as a sequence of independent lines (terminal output, most block explorers, and any bot/integration that splits on `\n` and regex-matches `Program (\S+) (success|failed|invoke)`), it will see fabricated lines that were never emitted by the runtime for the referenced program, alongside the genuine ones.

### Impact Explanation
This does not change the authoritative on-chain `TransactionError`/`meta.err` field, so it cannot forge consensus state. The impact is on the trust that ecosystem consumers place in the "stable log format" contract that Agave itself documents and guarantees. Consumers (custodians, bridges, exchange back-ends, or Anchor-derived clients) that determine execution outcome for a specific inner/CPI'd program by string-matching log lines rather than solely relying on `meta.err`/instruction indices can be misled into believing a different program succeeded/invoked/failed, potentially triggering fund release or other side effects (false execution acceptance) based on forged content that a fully unprivileged attacker's own program injected.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: exploitation requires a downstream integrator to parse `logMessages`/`logs` textually instead of relying on the transaction's authoritative status/err field and instruction indices — a known anti-pattern, but one that is documented to occur (log-based status/error detection is common in SDK tooling built on top of Agave's "stable log message" guarantee). The attack itself requires no privilege beyond deploying/invoking a program and calling `sol_log`, which any unprivileged actor can do.

### Recommendation
- Strip or escape control characters (particularly `\n`/`\r`) from program-supplied strings before inserting them into `stable_log::program_log`/`program_data`, or clearly delimit/length-prefix each log entry so it cannot span multiple logical lines.
- Alternatively, document explicitly (and enforce via serialization) that a single `logMessages` array element must never be split into multiple lines by consumers, and that consumers must never treat any log line as authoritative for the referenced program without cross-checking `stackHeight`/instruction index metadata already present in RPC responses.

### Proof of Concept
1. Deploy an unprivileged BPF program that, when invoked, calls `sol_log` with a message such as:
   `"ok\nProgram <VictimProgramId> success"`
2. Submit a transaction invoking this program via `simulateTransaction` or a confirmed transaction, then fetch it via `getTransaction`/`simulateTransaction`/`logsNotification`.
3. Observe that `meta.logMessages`/`logs` contains a single array element:
   `"Program log: ok\nProgram <VictimProgramId> success"`
   which, when rendered by any consumer that prints/splits the log content on `\n` (terminal, most explorers, naive parsers), displays as two separate lines — the second one being an indistinguishable forged "Program <VictimProgramId> success" line, even though `<VictimProgramId>` was never actually invoked in that transaction.

**Uncertainty**: I could not fully inspect the `ic_logger_msg!` macro definition (svm-log-collector/src/lib.rs lines 63-94) due to a tool error in the final iteration, so I cannot 100% rule out that some sanitization step exists there; based on the `LogCollector::log` implementation I did view, no such filtering exists. If a Devin session is started, this should be re-verified by reading that exact macro body and confirming no character escaping occurs before `LogCollector::log` is called.

### Citations

**File:** program-runtime/src/stable_log.rs (L1-4)
```rust
//! Stable program log messages
//!
//! The format of these log messages should not be modified to avoid breaking downstream consumers
//! of program logging
```

**File:** program-runtime/src/stable_log.rs (L20-31)
```rust
pub fn program_invoke(
    log_collector: &Option<Rc<RefCell<LogCollector>>>,
    program_id: &Pubkey,
    invoke_depth: usize,
) {
    ic_logger_msg!(
        log_collector,
        "Program {} invoke [{}]",
        program_id,
        invoke_depth
    );
}
```

**File:** program-runtime/src/stable_log.rs (L33-44)
```rust
/// Log a message from the program itself.
///
/// The general form is:
///
/// ```notrust
/// "Program log: <program-generated output>"
/// ```
///
/// That is, any program-generated output is guaranteed to be prefixed by "Program log: "
pub fn program_log(log_collector: &Option<Rc<RefCell<LogCollector>>>, message: &str) {
    ic_logger_msg!(log_collector, "Program log: {}", message);
}
```

**File:** program-runtime/src/stable_log.rs (L91-110)
```rust
/// "Program <address> success"
/// ```
pub fn program_success(log_collector: &Option<Rc<RefCell<LogCollector>>>, program_id: &Pubkey) {
    ic_logger_msg!(log_collector, "Program {} success", program_id);
}

/// Log program execution failure
///
/// The general form is:
///
/// ```notrust
/// "Program <address> failed: <program error details>"
/// ```
pub fn program_failure<E: std::fmt::Display>(
    log_collector: &Option<Rc<RefCell<LogCollector>>>,
    program_id: &Pubkey,
    err: &E,
) {
    ic_logger_msg!(log_collector, "Program {} failed: {}", program_id, err);
}
```

**File:** syscalls/src/logging.rs (L1-36)
```rust
use {
    super::*, solana_program_runtime::memory::translate_vm_slice, solana_sbpf::vm::ContextObject,
};

declare_builtin_function!(
    /// Log a user's info message
    SyscallLog,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        addr: u64,
        len: u64,
        _arg3: u64,
        _arg4: u64,
        _arg5: u64,
    ) -> Result<u64, Error> {
        let cost = invoke_context
            .get_execution_cost()
            .syscall_base_cost
            .max(len);
        invoke_context.compute_meter.consume_checked(cost)?;

        let check_aligned = invoke_context.get_check_aligned();
        let memory_mapping = invoke_context.memory_contexts.memory_mapping()?;
        translate_string_and_do(
            memory_mapping,
            addr,
            len,
            check_aligned,
            &mut |string: &str| {
                stable_log::program_log(&invoke_context.get_log_collector(), string);
                Ok(0)
            },
        )?;
        Ok(0)
    }
);
```

**File:** syscalls/src/lib.rs (L582-596)
```rust
/// Take a virtual pointer to a string (points to SBF VM memory space), translate it
/// pass it to a user-defined work function
fn translate_string_and_do(
    memory_mapping: &MemoryMapping,
    addr: u64,
    len: u64,
    check_aligned: bool,
    work: &mut dyn FnMut(&str) -> Result<u64, Error>,
) -> Result<u64, Error> {
    let buf = translate_slice::<u8>(memory_mapping, addr, len, check_aligned)?;
    match from_utf8(buf) {
        Ok(message) => work(message),
        Err(err) => Err(SyscallError::InvalidString(err, buf.to_vec()).into()),
    }
}
```

**File:** rpc/src/rpc.rs (L6653-6661)
```rust
                    "logs":[
                        "Program TestProgram11111111111111111111111111111111 invoke [1]",
                        "I am logging from a builtin program!",
                        "I am about to CPI to System!",
                        "Program 11111111111111111111111111111111 invoke [2]",
                        "Program 11111111111111111111111111111111 success",
                        "All done!",
                        "Program TestProgram11111111111111111111111111111111 success"
                    ],
```

**File:** rpc/src/rpc_subscriptions.rs (L2933-2954)
```rust
    fn make_logs_result(signature: &str, subscription_id: u64) -> serde_json::Value {
        json!({
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {
                        "slot": 0
                    },
                    "value": {
                        "signature": signature,
                        "err": null,
                        "logs": [
                            "Program 11111111111111111111111111111111 invoke [1]",
                            "Program 11111111111111111111111111111111 success"
                        ]
                    }
                },
                "subscription": subscription_id
            }
        })
    }
```
