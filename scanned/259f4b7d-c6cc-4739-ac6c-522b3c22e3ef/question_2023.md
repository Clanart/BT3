# Q2023: evict_using_random_selection artifact memory blowup

## Question
Can an unprivileged attacker reach `evict_using_random_selection` by submit transactions invoking deployed programs around cache pressure with many distinct program invocations, upgrade timing, and cache-pressure friendly workloads such that logs, return data, inner instructions, or side-channel artifacts created downstream scale much faster than request size, breaking the invariant that execution artifact growth must stay bounded per transaction and leading to `RPC DoS/Crash`?

## Target
- File/function: program-runtime/src/loaded_programs.rs::evict_using_random_selection
- Entrypoint: submit transactions invoking deployed programs around cache pressure
- Attacker controls: many distinct program invocations, upgrade timing, and cache-pressure friendly workloads
- Exploit idea: use legal execution artifacts as the amplifier instead of raw packet size
- Invariant to test: execution artifact growth must stay bounded per transaction
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run the same heavy transaction repeatedly and correlate artifact size with resident memory
