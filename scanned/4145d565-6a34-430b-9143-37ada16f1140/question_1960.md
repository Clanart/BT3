# Q1960: drain_modified_entries rollback dirty state

## Question
Can an unprivileged attacker reach `drain_modified_entries` by submit transactions invoking deployed programs around upgrade/close churn with upgrade timing, close/reopen timing, and repeated invocation of the same program id such that a failing transaction can leave dirty cache, balance, or metadata state behind even though execution is reported as failed, breaking the invariant that failed transactions must not leak state changes into later execution or rpc views and leading to `Consensus/Safety Violations`?

## Target
- File/function: program-runtime/src/loaded_programs.rs::drain_modified_entries
- Entrypoint: submit transactions invoking deployed programs around upgrade/close churn
- Attacker controls: upgrade timing, close/reopen timing, and repeated invocation of the same program id
- Exploit idea: search for post-failure state that survives into later reads or commits
- Invariant to test: failed transactions must not leak state changes into later execution or RPC views
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: force late failures after many writes and diff caches and post-state against a fresh bank reconstruction
