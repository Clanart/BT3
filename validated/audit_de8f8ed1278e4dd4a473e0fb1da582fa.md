## Analysis

**External report recap:** Apache DolphinScheduler leaked sensitive operational data into logs accessible to unauthorized actors — the failed invariant is that data explicitly meant to be withheld from a lower-trust audience (log viewers) was written into that channel anyway. The attacker-controlled input reached a logging sink through a path that had no field-level "don't print this" check.

**Protobuf analog:** Protobuf ships an explicit mechanism for exactly this invariant — the `debug_redact` field option, whose doc comment states it exists so a field's value is "not... printed out when using debug formats, e.g. when the field contains sensitive credentials" [1](#0-0) . This is enforced in `TextFormat::Printer` via `TryRedactFieldValue`/`GetRedactionState`, which replaces the value with a marker when `redact_debug_string_` is enabled [2](#0-1) .

However, the **parser's error path does not consult `debug_redact` at all**. In `TextFormat::ParserImpl::ConsumeFieldValue`, when a bool or enum token fails to parse/resolve, the raw attacker-supplied token text is embedded directly into the error message: [3](#0-2) [4](#0-3) 

That message flows through `ReportError` → `ReportErrorImpl`, which — if the caller hasn't installed a custom `io::ErrorCollector` — logs it unconditionally via `ABSL_LOG(ERROR)` including the message text and the root message's full type name: [5](#0-4) 

If an `ErrorCollector` *is* installed, the raw value is instead handed to `error_collector->RecordError(...)`, i.e., surfaced to whatever the calling application does with parse errors (often surfaced to end users/logs/monitoring — a lower-trust sink than the field's designated audience).

### Why this is a genuine bypass, not an allowed design choice
- `debug_redact` is a per-field, schema-declared "never print this in debug/log output" contract, honored by `DebugString()`/`PrintToString()`.
- The parser accepts a value for a `debug_redact` field, fails validation (invalid enum name, invalid bool literal), and echoes the exact attacker-supplied text into `ABSL_LOG(ERROR)` or the caller-supplied collector — with zero check of `field->options().debug_redact()`. There's no redaction call anywhere in `ConsumeFieldValue`'s error branches.
- This means a client submitting text-format input containing a bad value for a field explicitly marked sensitive (e.g., a malformed credential/token field typed as enum or bool) can force that value into process logs, defeating the exact protection `debug_redact` was designed to provide.

### Consuming-application exposure assumption
Applications that accept trusted schemas containing `debug_redact` fields and parse untrusted/semi-trusted TextFormat input (e.g., config ingestion, debug/admin APIs, textproto-based RPC bodies) rely on `debug_redact` to keep such values out of shared logs viewed by operators with lower trust than the field's intended audience — mirroring the DolphinScheduler log-exposure scenario.

### Title
Sensitive `debug_redact` field values leaked via unredacted TextFormat parser error messages - (File: `src/google/protobuf/text_format.cc`)

### Summary
`TextFormat::ParserImpl::ConsumeFieldValue` embeds the raw, attacker-supplied token text for invalid bool/enum field values directly into parse error messages, without checking the field's `debug_redact` option. These messages are either logged via `ABSL_LOG(ERROR)` (when no `ErrorCollector` is set) or forwarded verbatim to the caller-provided `ErrorCollector`, bypassing the redaction guarantee that `debug_redact` provides for `DebugString`/`PrintToString`.

### Finding Description
`debug_redact` is documented as preventing a field's value from being printed in debug formats [1](#0-0) , and `TextFormat::Printer` implements this via `TryRedactFieldValue` [2](#0-1) . The parsing path has no equivalent check: on an invalid boolean literal or unresolved enum name/number, `ConsumeFieldValue` builds an error string containing the literal parsed text and the field name [6](#0-5) [7](#0-6) , which is routed through `ReportErrorImpl` to `ABSL_LOG(ERROR)` or the collector [5](#0-4) .

### Impact Explanation
Sensitive field values (schema-declared as such via `debug_redact`) can be exposed to unauthorized actors who have read access to application logs or error-collector output but were never intended to see the field's contents — directly analogous to CWE-200 exposure via logs in the DolphinScheduler report.

### Likelihood Explanation
Requires: (1) a schema with a `debug_redact` bool/enum field, (2) an application that parses TextFormat input from a less-trusted source (config, debug endpoint, textproto RPC), and (3) that input containing an invalid value for that field. This is a plausible, low-complexity trigger — no special privileges needed beyond sending a supported parse call with a malformed field value.

### Recommendation
In `ConsumeFieldValue`'s error branches for `CPPTYPE_BOOL` and `CPPTYPE_ENUM` (and any other branch that echoes raw token text), check `field->options().debug_redact()` before including the value in the error message, replacing it with the same redaction marker (`kFieldValueReplacement`) used by the printer when redaction is enabled.

### Proof of Concept
Given a proto message with `optional MyEnum secret = 1 [debug_redact = true];`, parsing the TextFormat input `secret: "NOT_A_VALID_ENUM_NAME"` with no custom `ErrorCollector` set triggers `ReportError("Unknown enumeration value of \"NOT_A_VALID_ENUM_NAME\" for field \"secret\".")`, which is written verbatim to `ABSL_LOG(ERROR)` [7](#0-6) [8](#0-7)  — despite `debug_redact` being set on `secret`. (This trace is based on static code reading of `text_format.cc`; no live execution was performed in this environment.)

### Citations

**File:** src/google/protobuf/descriptor.proto (L776-778)
```text
  // Indicate that the field value should not be printed out when using debug
  // formats, e.g. when the field contains sensitive credentials.
  optional bool debug_redact = 16 [default = false];
```

**File:** src/google/protobuf/text_format.cc (L342-357)
```text
void ReportErrorImpl(int line, int col, absl::string_view message,
                     const Descriptor* root_message_type,
                     io::ErrorCollector* error_collector) {
  if (error_collector == nullptr) {
    if (line >= 0) {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << (line + 1)
                      << ":" << (col + 1) << ": " << message;
    } else {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << message;
    }
  } else {
    error_collector->RecordError(line, col, message);
  }
}
```

**File:** src/google/protobuf/text_format.cc (L1043-1051)
```text
          if (value == "true" || value == "True" || value == "t") {
            SET_FIELD(Bool, bool, true);
          } else if (value == "false" || value == "False" || value == "f") {
            SET_FIELD(Bool, bool, false);
          } else {
            ReportError(absl::StrCat("Invalid value for boolean field \"",
                                     field->name(), "\". Value: \"", value,
                                     "\"."));
            return false;
```

**File:** src/google/protobuf/text_format.cc (L1079-1093)
```text
        if (enum_value == nullptr) {
          if (int_value != kint64max &&
              !field->legacy_enum_field_treated_as_closed()) {
            SET_FIELD(EnumValue, int64, int_value);
            return true;
          } else if (!allow_unknown_enum_) {
            ReportError(absl::StrCat("Unknown enumeration value of \"", value,
                                     "\" for field \"", field->name(), "\"."));
            return false;
          } else {
            ReportWarning(absl::StrCat("Unknown enumeration value of \"", value,
                                       "\" for field \"", field->name(),
                                       "\"."));
            return true;
          }
```

**File:** src/google/protobuf/text_format.cc (L3279-3305)
```text
bool TextFormat::Printer::TryRedactFieldValue(
    const Message& message, const FieldDescriptor* field,
    BaseTextGenerator* generator, bool insert_value_separator) const {
  TextFormat::RedactionState redaction_state =
      DescriptorPool::MemoizeProjection(
          field, [](const FieldDescriptor* field) {
            return TextFormat::GetRedactionState(field);
          });
  if (redaction_state.redact) {
    if (redact_debug_string_) {
      IncrementRedactedFieldCounter();
      if (insert_value_separator) {
        generator->PrintMaybeWithMarker(MarkerToken(), ": ");
      }
      generator->PrintString(kFieldValueReplacement);
      if (insert_value_separator) {
        if (single_line_mode_) {
          generator->PrintLiteral(" ");
        } else {
          generator->PrintLiteral("\n");
        }
      }
      return true;
    }
  }
  return false;
}
```
