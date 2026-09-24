Based on my research, this report describes a Solidity-specific state-management bug in a DeFi smart contract: an off-chain queue (`rolloverQueue`) index mapping (`ownerToRollOverQueueIndex`) is updated unconditionally outside the `if`/`else` branch in `enlistInRollover`, letting an attacker desynchronize their queue-index pointer from another user's queue slot, then reclaim/replace that user's entry via `delistInRollover`. The failed invariant is "each account's persistent index into a mutable, shared, append/pop list must always point to that same account's own list entry across sequential calls."

This bug class depends on: (1) a long-lived, cross-call, per-account mapping into a mutable array that grows/shrinks via push/pop, (2) attacker-controlled call ordering across multiple transactions/blocks, and (3) reuse of a stale index after another party's insertion. None of these preconditions exist in Protobuf's parsing surface:

- Protobuf's binary/ProtoJSON parsers (`ParseContext`/`TcParser` in C++, `MessageSchema`/`ArrayDecoders` in Java, upb wire/JSON in Python/Ruby/PHP, `MessageParser`/`JsonParser` in C#) operate on a single bounded input buffer within one parse call; there is no persistent, cross-call, attacker-influenced index-to-slot mapping analogous to `ownerToRollOverQueueIndex`. [1](#0-0) 
- Oneof-case bookkeeping, which is the closest conceptual analog (a "current selected slot" indicator updated during parsing), correctly swaps the case value and disposes of the previous member atomically within the same function call — there is no window where a stale index from a prior, unrelated insertion can be reused by a different field/message. [2](#0-1) 
- Java's oneof parsing sets the case tag together with the field write in the same switch branch, with no separate mapping structure that could be updated outside its owning branch.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L1989-2007)
```text
void TcParser::ChangeOneof(const TcParseTableBase* table,
                           const ClassData* class_data,
                           const TcParseTableBase::FieldEntry& entry,
                           uint32_t field_num, ParseContext* ctx,
                           MessageLite* msg) {
  // The _oneof_case_ value offset is stored in the has-bit index.
  uint32_t* oneof_case = &TcParser::RefAt<uint32_t>(msg, entry.has_idx);
  uint32_t current_case = *oneof_case;
  *oneof_case = field_num;

  // If the member is already active, then it should be merged. We're done.
  if (current_case == field_num) return;

  if (current_case == 0) {
    // If the member is empty, we don't have anything to clear.
    // We must create a new member object.
    InitOneof(table, class_data, entry, msg);
    return;
  }
```
